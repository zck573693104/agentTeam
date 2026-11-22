"""P5 housekeeping 测试:观察模式蒸馏回路 / 记忆淘汰 / 工作区归档。"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from agentteam.api.server import create_app
from agentteam.models.provider import ModelRef
from agentteam.v2.api.router import build_v2_router
from agentteam.v2.domain.worker import WorkerDef
from agentteam.v2.kernel.eval_distill import EvalRules, InboundDistiller
from agentteam.v2.kernel.eval_engine import EvalDecision
from agentteam.v2.kernel.memory import LessonDraft, MemoryStore
from agentteam.v2.kernel.router import RouterDecision
from agentteam.v2.kernel.workspace import WorkspaceManager
from tests.conftest import FakeLLM, FakeModelProvider
from tests.v2.conftest import wait_ticket_terminal


def _backdate_lesson(path: Path, days: int, use_count: int = 0) -> None:
    """把 lesson 文件的 frontmatter 改为 days 天前创建(模拟陈旧)。"""
    iso = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"created_at: [^\n]+", f"created_at: {iso}", text)
    text = re.sub(r"use_count: [^\n]+", f"use_count: {use_count}", text)
    path.write_text(text, encoding="utf-8")


# —— 记忆淘汰 ——
def test_memory_evict_stale(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory")
    store.add_lessons(None, [
        LessonDraft(condition="陈旧未命中", lesson="该被归档"),
        LessonDraft(condition="新教训", lesson="留下"),
    ])
    files = {p.read_text(encoding="utf-8"): p
             for p in (tmp_path / "memory" / "lessons").glob("les_*.md")}
    assert len(files) == 2
    _backdate_lesson(next(p for t, p in files.items() if "陈旧未命中" in t), days=60)
    _backdate_lesson(next(p for t, p in files.items() if "新教训" in t), days=1)

    evicted = store.evict_stale(30)
    assert len(evicted) == 1
    archive = list((tmp_path / "memory" / "lessons" / "_archive").glob("les_*.md"))
    assert len(archive) == 1 and "该被归档" in archive[0].read_text(encoding="utf-8")
    # 索引重建:只剩新鲜条目
    index = (tmp_path / "memory" / "lessons" / "_index.md").read_text(encoding="utf-8")
    assert "新教训" in index and "陈旧未命中" not in index
    # 检索/列表不再看到已归档条目
    assert len(store.list_lessons()) == 1
    # 0 = 关闭
    assert store.evict_stale(0) == []


def test_memory_evict_keeps_used_lessons(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory")
    store.add_lessons(None, [LessonDraft(condition="c", lesson="l")])
    path = next((tmp_path / "memory" / "lessons").glob("les_*.md"))
    _backdate_lesson(path, days=60, use_count=2)  # 命中过的永不淘汰
    assert store.evict_stale(30) == []
    assert path.exists()


# —— 工作区归档 ——
def test_workspace_archive_stale(tmp_path: Path):
    ws_root = tmp_path / "workspaces"
    mgr = WorkspaceManager(ws_root)
    old_iso = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    fresh_iso = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    mgr.for_ticket("t_old")
    (ws_root / "t_old" / "out.txt").write_text("产出", encoding="utf-8")
    mgr.for_ticket("t_fresh")
    (ws_root / "t_fresh" / "keep.txt").write_text("x", encoding="utf-8")

    archived = mgr.archive_stale(
        [("t_old", old_iso), ("t_fresh", fresh_iso), ("t_missing", old_iso)], max_age_days=7)
    assert archived == ["t_old"]
    assert not (ws_root / "t_old").exists()
    assert (ws_root / "_archive" / "t_old" / "out.txt").read_text(encoding="utf-8") == "产出"
    assert (ws_root / "t_fresh" / "keep.txt").exists()  # 未超期保留
    # 0 = 关闭
    assert mgr.archive_stale([("t_fresh", old_iso)], max_age_days=0) == []


# —— 观察模式蒸馏回路(单元) ——
def test_inbound_distiller_round(tmp_path: Path):
    llm = FakeLLM()
    llm.set_structured_responses([EvalRules(rules=[
        "样本共性:要求修改具体页面并给出验收标准的请求应接受",
        "样本共性:纯提问(无实施意图)的变体应拒绝",
    ])])
    distiller = InboundDistiller(tmp_path / "inbound",
                                 FakeModelProvider({"qwen-turbo": llm}),
                                 ModelRef("qwen", "qwen-turbo"), threshold=3)
    # 阈值未到 → 不蒸馏
    assert distiller.record_confirm("修登录页报错") == 1
    assert distiller.maybe_distill() is None
    assert distiller.record_confirm("优化首页加载速度") == 2
    # 攒够阈值 → 蒸馏落盘,样本清空
    distiller.record_confirm("重构导出逻辑,验收:导出不超时")
    rules = distiller.maybe_distill()
    assert rules and "验收标准" in rules[0]
    assert distiller.pending_count() == 0
    assert (tmp_path / "inbound" / "eval_rules.md").exists()
    assert len(distiller.lines()) == 2
    # 蒸馏 LLM 失败 → 样本已被取走,安静降级
    distiller.record_confirm("a")
    distiller.record_confirm("b")
    distiller.record_confirm("c")
    llm.set_structured_responses([])  # 队列空 → invoke 抛 IndexError
    assert distiller.maybe_distill() is None
    assert distiller.pending_count() == 0


# —— 全链路:确认 → 蒸馏 → Eval prompt 注入校准规则 ——
def _make_client(make_v2, llm_by_model, **kw):
    v2 = make_v2(llm_by_model, **kw)
    app = FastAPI()
    app.include_router(build_v2_router(v2), prefix="/api/v2")
    return TestClient(app), v2


def test_confirm_distills_rules_into_eval_prompt(make_v2, tmp_path: Path):
    """观察模式确认 ×5 → housekeeping 蒸馏 → 下一次 Eval prompt 带上【历史校准规则】。"""
    main, aux = FakeLLM(), FakeLLM()
    main.set_structured_responses([
        RouterDecision(action="dispatch", worker="coder", goal="g") for _ in range(5)
    ])
    main.set_invoke_responses([AIMessage(content="done") for _ in range(5)])
    aux.set_structured_responses(
        [EvalDecision(accept=True, reason="三事实成立") for _ in range(5)]
        + [EvalRules(rules=["共性规则:给出明确验收标准的修复请求应接受"])]
        + [EvalDecision(accept=True, reason="三事实成立")]
    )
    client, v2 = _make_client(
        make_v2, {"qwen-max": main, "qwen-turbo": aux},
        memory_dir=tmp_path / "memory", aux_model_name="qwen-turbo",
        eval_distill_threshold=5,
    )
    v2.worker_repo.upsert(WorkerDef(name="coder"))
    client.post("/api/v2/sources", json={"name": "ops", "kind": "webhook",
                                         "config": {"mode": "observe"}})
    # 5 次观察 + 5 次人工确认(每次转正的工单终态后,housekeeping 尝试蒸馏)
    for i in range(5):
        body = client.post("/api/v2/sources/ops/webhook", json={
            "actor": "alice", "thread_key": f"chat-{i}", "text": f"修复任务 {i}",
        }).json()
        assert body["decision"] == "would_accept"
        confirm = client.post(f"/api/v2/sources/observe/{body['evaluation_id']}/confirm",
                              json={"by": "boss"})
        assert confirm.status_code == 200
        wait_ticket_terminal(v2, confirm.json()["ticket_id"])
    # 等第 5 个工单的 housekeeping 蒸馏落盘
    rules_path = tmp_path / "memory" / "inbound" / "eval_rules.md"
    deadline = time.time() + 5
    while time.time() < deadline and not rules_path.exists():
        time.sleep(0.05)
    assert rules_path.exists(), "确认样本攒够后应蒸馏出校准规则"
    assert "共性规则" in rules_path.read_text(encoding="utf-8")
    # 第 6 次入站:Eval prompt 注入校准规则(EvalEngine 的 prompt 是纯字符串)
    resp = client.post("/api/v2/sources/ops/webhook", json={
        "actor": "alice", "thread_key": "chat-99", "text": "又一个修复任务",
    })
    assert resp.status_code == 200
    eval_prompts = [c["messages"] for c in aux.invoked_structured
                    if getattr(c["schema"], "__name__", "") == "EvalDecision"]
    assert any("【历史校准规则】" in str(p) for p in eval_prompts)
    # 控制台 API 可见
    mem = client.get("/api/v2/memory").json()
    assert mem["enabled"] and len(mem["inbound_rules"]) == 1


def test_postmortem_housekeeping_archives_stale_workspace(make_v2, tmp_path: Path):
    """复盘角色兼职保洁:超期终态工单工作区归档(直接调 postmortem.run 验证)。"""
    mem = tmp_path / "memory"
    llm = FakeLLM()
    llm.set_invoke_responses([AIMessage(content="done")])
    v2 = make_v2({"qwen-max": llm}, memory_dir=mem, workspace_archive_days=1)
    _add = WorkerDef(name="coder")
    v2.worker_repo.upsert(_add)
    tid = v2.scheduler.submit_request("x", worker_name="coder")["ticket_id"]
    wait_ticket_terminal(v2, tid)
    workspace = Path(v2.session_repo.list_for_ticket(tid)[0]["workspace_dir"])
    assert workspace.exists()
    # 把终态时间回拨 10 天(模拟陈旧)
    stale_iso = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    v2.conn.execute("UPDATE tickets SET ended_at = ? WHERE id = ?", (stale_iso, tid))
    v2.conn.commit()
    # 再次触发 postmortem(幂等跳过复盘,但 housekeeping 仍执行)
    v2.postmortem.run(tid)
    assert not workspace.exists()
    archived = tmp_path / "workspaces" / "_archive" / tid
    assert archived.is_dir()


def test_memory_endpoint_disabled_without_memory_dir(make_v2):
    """未配置记忆库(默认)→ /api/memory 显式 disabled;旧测试零行为变化。"""
    client, v2 = _make_client(make_v2, None)
    body = client.get("/api/v2/memory").json()
    assert body == {"enabled": False, "lessons": [], "inbound_rules": []}
    assert v2.postmortem is None and v2.distiller is None


def test_create_app_default_wires_memory(tmp_path: Path):
    """server 工厂默认(memory 目录随 db)装配记忆库/复盘;显式 off 关闭。"""
    (tmp_path / "db").mkdir(parents=True)
    (tmp_path / "db2").mkdir(parents=True)
    app = create_app(db_path=str(tmp_path / "db" / "x.sqlite"), web_dist=None)
    with TestClient(app) as client:
        body = client.get("/api/memory").json()
        assert body["enabled"] is True
    # 显式 off
    import os
    os.environ["AGENTTEAM_V2_MEMORY_DIR"] = "off"
    try:
        app2 = create_app(db_path=str(tmp_path / "db2" / "y.sqlite"), web_dist=None)
        with TestClient(app2) as client:
            assert client.get("/api/memory").json()["enabled"] is False
    finally:
        os.environ.pop("AGENTTEAM_V2_MEMORY_DIR", None)
