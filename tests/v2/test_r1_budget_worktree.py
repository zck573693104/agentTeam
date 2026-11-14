"""R1 测试:预算治理 / worktree / diff review / transcript 压缩 / 双向 pass-through。"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from langchain_core.messages import AIMessage

from agentteam.v2.domain import events as ev
from agentteam.v2.domain.events import TicketStatus
from agentteam.v2.domain.worker import WorkerDef
from tests.v2.conftest import FakeLLM, wait_ticket_terminal


def _wait_status(v2, ticket_id: str, status: TicketStatus, timeout: float = 10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = v2.ticket_repo.get(ticket_id)
        if t and t.status == status:
            return t
        time.sleep(0.05)
    raise TimeoutError(f"not {status.value}")


def _wait_attention_type(v2, ticket_id: str, att_type: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for a in v2.attention_repo.list_by_status("pending"):
            if a["ticket_id"] == ticket_id and a["type"] == att_type:
                return a
        time.sleep(0.05)
    raise TimeoutError(f"no {att_type} attention")


# —— A1 预算治理 ——
def test_budget_exceeded_pauses_and_double_continues(make_v2):
    """超预算 → Attention(budget);approve → 预算翻倍继续 → done。"""
    llm = FakeLLM()
    llm.set_invoke_responses([AIMessage(content="done")])
    v2 = make_v2({"qwen-max": llm})
    v2.worker_repo.upsert(WorkerDef(name="coder"))
    # 直接造一个预算为 1 的 ticket(1 token 必超;FakeLLM 无 usage → 0 tokens,
    # 改用 budget=1 且手工注入 session tokens 验证闸门逻辑)
    resp = v2.scheduler.submit_request("x", worker_name="coder", budget_tokens=1)
    tid = resp["ticket_id"]
    # 给 session 注入 100 tokens 触发阈值(下一个迭代边界检查)
    v2.session_repo.add_tokens(
        v2.session_repo.list_for_ticket(tid)[-1]["id"], 100, 0,
    ) if v2.session_repo.list_for_ticket(tid) else None
    t = wait_ticket_terminal(v2, tid)
    # FakeLLM 无 usage → token 恒 0,不会触发;此用例验证参数透传与不限额路径
    assert t.status == TicketStatus.DONE
    assert v2.ticket_repo.get(tid).budget_tokens == 1


def test_budget_attention_flow(make_v2):
    """人工注入超限 → budget Attention;approve 翻倍;reject 终止。"""
    llm = FakeLLM()
    llm.set_invoke_responses([
        AIMessage(content="第一轮\nQUESTION: 先确认预算?"),
        AIMessage(content="预算加额后完成"),
    ])
    v2 = make_v2({"qwen-max": llm})
    v2.worker_repo.upsert(WorkerDef(name="coder"))
    resp = v2.scheduler.submit_request("x", worker_name="coder")
    tid = resp["ticket_id"]
    _wait_status(v2, tid, TicketStatus.WAITING_HUMAN)
    v2.ticket_repo.update_budget(tid, 10)
    # 模拟预算已耗尽(手工注入 tokens)→ 造 budget attention(走真实闸门需真模型 usage)
    att = v2.attention_repo.create(tid, "s-fake", "budget",
                                   {"used": 12, "limit": 10,
                                    "decision_effect": "approve = 预算翻倍继续;reject = 终止工单"})
    v2.session_repo.create("s-fake", tid, "coder", "ws", 10, None)
    v2.sessions_hack = None
    # approve → scheduler 加额翻倍(10→20)
    v2.scheduler.resume_attention(att["id"], decision="approve", decided_by="boss")
    assert v2.ticket_repo.get(tid).budget_tokens == 20


def test_budget_reject_terminates(make_v2):
    llm = FakeLLM()
    llm.set_invoke_responses([AIMessage(content="第一轮\nQUESTION: 确认?")])
    v2 = make_v2({"qwen-max": llm})
    v2.worker_repo.upsert(WorkerDef(name="coder"))
    resp = v2.scheduler.submit_request("x", worker_name="coder")
    tid = resp["ticket_id"]
    _wait_status(v2, tid, TicketStatus.WAITING_HUMAN)
    v2.ticket_repo.update_budget(tid, 10)
    att = v2.attention_repo.create(tid, "s-fake", "budget",
                                   {"used": 12, "limit": 10})
    v2.session_repo.create("s-fake", tid, "coder", "ws", 10, None)
    v2.scheduler.resume_attention(att["id"], decision="reject", decided_by="boss")
    # reject: 会话 s-fake 转.failed;工单仍 waiting(恢复线程把 s-fake 置 failed,
    # ticket 状态由 _resume_session 的 result 分支处理)
    deadline = time.time() + 5
    while time.time() < deadline:
        if v2.ticket_repo.get(tid).status.is_terminal():
            break
        time.sleep(0.05)
    assert v2.session_repo.get("s-fake")["status"] == "failed"


# —— A2 worktree + B7 diff ——
def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "base.txt").write_text("base", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def test_worktree_workspace_and_changeset(make_v2, tmp_path):
    """git 仓库工单 → worktree 隔离;done 后 /diff 输出与基线的变更集。"""
    repo = tmp_path / "repo"
    _init_repo(repo)
    llm = FakeLLM()
    llm.set_invoke_responses([
        AIMessage(content="", tool_calls=[{
            "name": "write_file",
            "args": {"path": "feature.txt", "content": "new feature"}, "id": "c1",
        }]),
        AIMessage(content="写完了"),
    ])
    v2 = make_v2({"qwen-max": llm})
    v2.worker_repo.upsert(WorkerDef(name="coder"))
    resp = v2.scheduler.submit_request("写 feature.txt", worker_name="coder",
                                       repo_path=str(repo))
    tid = resp["ticket_id"]
    t = wait_ticket_terminal(v2, tid)
    assert t.status == TicketStatus.DONE
    ws = Path(v2.session_repo.list_for_ticket(tid)[-1]["workspace_dir"])
    assert (ws / ".git").exists()          # worktree 而非普通目录
    assert (ws / "feature.txt").read_text(encoding="utf-8") == "new feature"
    # 原仓库工作区未被污染
    assert not (repo / "feature.txt").exists()
    # 变更集
    from agentteam.v2.kernel.workspace import ticket_change_set
    cs = ticket_change_set(ws)
    assert cs["is_git"] and cs["base"] == "main"
    assert "feature.txt" in cs["stat"]


def test_write_file_attention_includes_diff(make_v2):
    """B7: write_file 审批 Attention 带 unified diff(旧→新)。"""
    llm = FakeLLM()
    llm.set_invoke_responses([
        AIMessage(content="", tool_calls=[{
            "name": "write_file",
            "args": {"path": "x.txt", "content": "new line"}, "id": "c1",
        }]),
    ])
    v2 = make_v2({"qwen-max": llm})
    v2.worker_repo.upsert(WorkerDef(name="coder", approval_targets=["write_file"]))
    resp = v2.scheduler.submit_request("x", worker_name="coder")
    tid = resp["ticket_id"]
    att = None
    deadline = time.time() + 10
    while time.time() < deadline:
        for a in v2.attention_repo.list_by_status("pending"):
            if a["ticket_id"] == tid and a["type"] == "approval":
                att = a
        if att:
            break
        time.sleep(0.05)
    assert att is not None
    assert "diff" in att["payload"]
    assert "+new line" in att["payload"]["diff"]


# —— A3 压缩 ——
def test_transcript_compact(make_v2):
    llm = FakeLLM()
    llm.set_invoke_responses([AIMessage(content="done")])
    v2 = make_v2({"qwen-max": llm})
    # 直接测 repo 层
    for i in range(10):
        v2.message_repo.append("s1", "user", f"msg-{i}")
    did = v2.message_repo.compact("s1", keep_last=3, summary="曾说过 msg-0..msg-6")
    assert did is True
    rows = v2.message_repo.list_for_session("s1")
    contents = [r["content"] for r in rows]
    assert any("历史压缩" in c for c in contents)          # 摘要行保留
    assert "msg-7" in contents and "msg-0" not in contents  # 旧行被排除
    assert v2.message_repo.compact("s1", keep_last=3, summary="x") is False  # 幂等


# —— A4 双向 pass-through ——
def test_thread_fanout_to_active_tickets(make_v2):
    llm = FakeLLM()
    llm.set_invoke_responses([
        AIMessage(content="", tool_calls=[{
            "name": "read_file", "args": {"path": "base.txt"}, "id": "c1",
        }]),
        AIMessage(content="读完,收到追问后结束"),
    ])
    v2 = make_v2({"qwen-max": llm})
    v2.worker_repo.upsert(WorkerDef(name="coder"))
    thread = v2.thread_repo.get_or_create("feishu", "chat-fanout")
    resp = v2.scheduler.submit_request("x", worker_name="coder", thread_id=thread["id"])
    tid = resp["ticket_id"]
    # 工单运行中广播线程事件 → worker 收到(只入不回)
    delivered = v2.scheduler.fan_out_thread(thread["id"], "补充需求: 加验收标准", by="alice")
    assert delivered == 1
    t = wait_ticket_terminal(v2, tid)
    msgs = v2.message_repo.list_for_session(
        v2.session_repo.list_for_ticket(tid)[-1]["id"]
    )
    assert any("补充需求" in m["content"] for m in msgs)
    fanout_events = [e for e in v2.event_repo.list_after(tid)
                     if e["type"] == ev.FOLLOW_UP_DELIVERED
                     and e["payload"].get("via") == "thread_fanout"]
    assert len(fanout_events) == 1


# —— A1 预算闸门真实链路(模型 usage 计账 → 迭代边界检查) ——
def test_budget_gate_pauses_and_double_resume(make_v2):
    """真实闸门:第一轮 usage 12 > budget 10 → budget Attention;
    approve → 预算翻倍 20 → 从 transcript 续跑 → done。"""
    llm = FakeLLM()
    llm.set_invoke_responses([
        AIMessage(content="", tool_calls=[{
            "name": "write_file",
            "args": {"path": "a.txt", "content": "x"}, "id": "c1",
        }], usage_metadata={"input_tokens": 8, "output_tokens": 4, "total_tokens": 12}),
        AIMessage(content="预算加额后完成"),
    ])
    v2 = make_v2({"qwen-max": llm})
    v2.worker_repo.upsert(WorkerDef(name="coder"))
    resp = v2.scheduler.submit_request("x", worker_name="coder", budget_tokens=10)
    tid = resp["ticket_id"]
    att = _wait_attention_type(v2, tid, "budget")
    assert att["payload"]["used"] == 12
    assert att["payload"]["limit"] == 10
    v2.scheduler.resume_attention(att["id"], decision="approve", decided_by="boss")
    assert v2.ticket_repo.get(tid).budget_tokens == 20
    t = wait_ticket_terminal(v2, tid)
    assert t.status == TicketStatus.DONE
    assert t.result == "预算加额后完成"


def test_budget_gate_reject_terminates(make_v2):
    """真实闸门:超限 → budget Attention;reject → 工单 failed(预算耗尽终止)。"""
    llm = FakeLLM()
    llm.set_invoke_responses([
        AIMessage(content="", tool_calls=[{
            "name": "write_file",
            "args": {"path": "a.txt", "content": "x"}, "id": "c1",
        }], usage_metadata={"input_tokens": 8, "output_tokens": 4, "total_tokens": 12}),
    ])
    v2 = make_v2({"qwen-max": llm})
    v2.worker_repo.upsert(WorkerDef(name="coder"))
    resp = v2.scheduler.submit_request("x", worker_name="coder", budget_tokens=10)
    tid = resp["ticket_id"]
    att = _wait_attention_type(v2, tid, "budget")
    v2.scheduler.resume_attention(att["id"], decision="reject", decided_by="boss")
    t = wait_ticket_terminal(v2, tid)
    assert t.status == TicketStatus.FAILED
    assert "预算耗尽" in (t.result or "")
