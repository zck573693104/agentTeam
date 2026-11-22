"""P4 学习闭环测试:终态复盘蒸馏 → 文件式记忆 → 注入 → 技能晋升(FakeLLM 全链路)。"""
from __future__ import annotations

import time
from pathlib import Path

from langchain_core.messages import AIMessage

from agentteam.v2.domain import events as ev
from agentteam.v2.domain.events import TicketStatus
from agentteam.v2.domain.worker import WorkerDef
from agentteam.v2.kernel.memory import LessonDraft, MemoryStore
from agentteam.v2.kernel.postmortem import LessonItem, PostmortemResult, SkillProposal
from tests.v2.conftest import FakeLLM, wait_ticket_terminal


def _add_worker(v2, name="coder", **kw):
    v2.worker_repo.upsert(WorkerDef(name=name, **kw))
    return name


def _wait_for_event(v2, ticket_id: str, event_type: str, timeout: float = 5.0) -> dict:
    """轮询事件流直到出现指定事件(复盘在独立线程池,需等待)。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for e in v2.event_repo.list_after(ticket_id):
            if e["type"] == event_type:
                return e
        time.sleep(0.05)
    raise TimeoutError(f"{event_type} not seen for ticket {ticket_id}")


# 一次「工具调用 + 收尾」的会话(5 条消息 > _MIN_MESSAGES,确保触发复盘)
_TOOL_THEN_DONE = [
    AIMessage(content="", tool_calls=[{
        "name": "write_file", "args": {"path": "a.txt", "content": "x"}, "id": "c1",
    }]),
    AIMessage(content="done"),
]


def test_done_ticket_distills_lesson(make_v2, tmp_path: Path):
    mem = tmp_path / "memory"
    llm = FakeLLM()
    llm.set_invoke_responses(list(_TOOL_THEN_DONE))
    llm.set_structured_responses([PostmortemResult(
        outcome_summary="完成",
        lessons=[LessonItem(condition="写文件前先确认目录存在", lesson="先 list_dir 再 write_file",
                            scope="global")],
    )])
    v2 = make_v2({"qwen-max": llm}, memory_dir=mem)
    _add_worker(v2)
    resp = v2.scheduler.submit_request("写 a.txt", worker_name="coder")
    assert wait_ticket_terminal(v2, resp["ticket_id"]).status == TicketStatus.DONE
    event = _wait_for_event(v2, resp["ticket_id"], ev.POSTMORTEM_COMPLETED)
    assert event["payload"]["lesson_count"] == 1
    # L2 教训文件 + L0 索引落盘
    lesson_files = list((mem / "lessons").glob("les_*.md"))
    assert len(lesson_files) == 1
    text = lesson_files[0].read_text(encoding="utf-8")
    assert "list_dir" in text and "use_count: 0" in text
    index = (mem / "lessons" / "_index.md").read_text(encoding="utf-8")
    assert "- les_" in index


def test_postmortem_dedup_merges_identical_lesson(make_v2, tmp_path: Path):
    mem = tmp_path / "memory"
    same = LessonItem(condition="运行测试前先清理产物目录", lesson="先清 dist 再构建",
                      scope="global")
    llm = FakeLLM()
    llm.set_invoke_responses(list(_TOOL_THEN_DONE) + list(_TOOL_THEN_DONE))
    llm.set_structured_responses([
        PostmortemResult(lessons=[same]), PostmortemResult(lessons=[same]),
    ])
    v2 = make_v2({"qwen-max": llm}, memory_dir=mem)
    _add_worker(v2)
    # 顺序提交(共享 FakeLLM 队列,并发会交错消费响应;复盘等待保证确定性)
    ids = []
    for _ in range(2):
        tid = v2.scheduler.submit_request("x", worker_name="coder")["ticket_id"]
        assert wait_ticket_terminal(v2, tid).status == TicketStatus.DONE
        _wait_for_event(v2, tid, ev.POSTMORTEM_COMPLETED)
        ids.append(tid)
    # 同文判重 → 合并,不重复立项
    assert len(list((mem / "lessons").glob("les_*.md"))) == 1


def test_failed_ticket_also_distills(make_v2, tmp_path: Path):
    mem = tmp_path / "memory"
    llm = FakeLLM()
    # max_iterations=1 且首轮就要调用未知工具 → 迭代耗尽 FAILED
    llm.set_invoke_responses([
        AIMessage(content="", tool_calls=[{"name": "nope", "args": {}, "id": "c1"}]),
    ])
    llm.set_structured_responses([PostmortemResult(
        outcome_summary="失败",
        lessons=[LessonItem(condition="工具名拼错时", lesson="先查工具注册表再调用",
                            scope="global")],
    )])
    v2 = make_v2({"qwen-max": llm}, memory_dir=mem)
    _add_worker(v2, max_iterations=1)
    resp = v2.scheduler.submit_request("x", worker_name="coder")
    assert wait_ticket_terminal(v2, resp["ticket_id"]).status == TicketStatus.FAILED
    _wait_for_event(v2, resp["ticket_id"], ev.POSTMORTEM_COMPLETED)
    assert len(list((mem / "lessons").glob("les_*.md"))) == 1


def test_trivial_session_skips_postmortem(make_v2, tmp_path: Path):
    """直答会话(system+user+终答 = 3 行)不值得蒸馏 → 无 postmortem 事件。"""
    mem = tmp_path / "memory"
    llm = FakeLLM()
    llm.set_invoke_responses([AIMessage(content="done")])
    v2 = make_v2({"qwen-max": llm}, memory_dir=mem)
    _add_worker(v2)
    resp = v2.scheduler.submit_request("x", worker_name="coder")
    wait_ticket_terminal(v2, resp["ticket_id"])
    time.sleep(0.5)  # 给误触发的复盘留暴露窗口
    types = [e["type"] for e in v2.event_repo.list_after(resp["ticket_id"])]
    assert ev.POSTMORTEM_COMPLETED not in types
    assert not list((mem / "lessons").glob("les_*.md"))


def test_memory_injected_into_worker_prompt(make_v2, tmp_path: Path):
    mem = tmp_path / "memory"
    MemoryStore(mem).add_lessons(None, [
        LessonDraft(condition="在该仓库跑测试要加 -x", lesson="pytest -x 防止超时"),
    ])
    llm = FakeLLM()
    llm.set_invoke_responses([AIMessage(content="done")])
    v2 = make_v2({"qwen-max": llm}, memory_dir=mem)
    _add_worker(v2)
    resp = v2.scheduler.submit_request("跑一下测试", worker_name="coder")
    wait_ticket_terminal(v2, resp["ticket_id"])
    system = llm.invoked_messages[0][0].content
    assert "【历史教训】" in system and "pytest -x" in system
    # 命中即计数(record_use)
    lesson_file = next((mem / "lessons").glob("les_*.md"))
    assert "use_count: 1" in lesson_file.read_text(encoding="utf-8")


def test_skill_proposal_promote_and_reject(make_v2, tmp_path: Path):
    mem = tmp_path / "memory"
    skills = tmp_path / "skills"
    skills.mkdir()
    llm = FakeLLM()
    llm.set_invoke_responses(list(_TOOL_THEN_DONE) + list(_TOOL_THEN_DONE))
    llm.set_structured_responses([
        PostmortemResult(outcome_summary="ok", skill_proposal=SkillProposal(
            name="Deploy Check!", content="# deploy check\n1. 先跑测试\n2. 再发布",
            summary="部署前自检流程")),
        PostmortemResult(outcome_summary="ok", skill_proposal=SkillProposal(
            name="another one", content="# another\n流程 B", summary="另一流程")),
    ])
    v2 = make_v2({"qwen-max": llm}, memory_dir=mem, skills_dir=skills)
    _add_worker(v2)
    # 顺序提交(原因同上:共享 FakeLLM 队列需确定性消费)
    ids = []
    for _ in range(2):
        tid = v2.scheduler.submit_request("x", worker_name="coder")["ticket_id"]
        assert wait_ticket_terminal(v2, tid).status == TicketStatus.DONE
        _wait_for_event(v2, tid, ev.POSTMORTEM_COMPLETED)
        ids.append(tid)
    proposals = [a for a in v2.attention_repo.list_by_status("pending")
                 if a["type"] == "proposal"]
    assert len(proposals) == 2
    # 工单已终态:提案决定不恢复会话、不改工单状态(dsh 状态机红线)
    status_before = v2.ticket_repo.get(ids[0]).status
    v2.scheduler.resume_attention(proposals[0]["id"], decision="approve", decided_by="test")
    assert v2.ticket_repo.get(ids[0]).status == status_before
    # 批准 → 文件落盘(名字收敛) + 技能库热加载
    assert (skills / "deploy-check.md").exists()
    assert "deploy-check" in v2.skill_loader.list_available()
    # 拒绝 → 不落盘
    v2.scheduler.resume_attention(proposals[1]["id"], decision="reject", decided_by="test")
    assert not (skills / "another-one.md").exists()
    decided = {a["id"]: a["decision"] for a in v2.attention_repo.list_by_status("decided")}
    assert decided[proposals[0]["id"]] == "approve"
    assert decided[proposals[1]["id"]] == "reject"


def test_postmortem_llm_failure_isolated(make_v2, tmp_path: Path):
    """复盘 LLM 失败 → postmortem_failed 事件;工单状态与记忆库零污染。"""
    mem = tmp_path / "memory"
    llm = FakeLLM()
    llm.set_invoke_responses(list(_TOOL_THEN_DONE))  # structured 队列空 → invoke 抛 IndexError
    v2 = make_v2({"qwen-max": llm}, memory_dir=mem)
    _add_worker(v2)
    resp = v2.scheduler.submit_request("x", worker_name="coder")
    assert wait_ticket_terminal(v2, resp["ticket_id"]).status == TicketStatus.DONE
    _wait_for_event(v2, resp["ticket_id"], ev.POSTMORTEM_FAILED)
    assert not list((mem / "lessons").glob("les_*.md"))


def test_recall_prefers_repo_scope_and_no_hit_is_empty(tmp_path: Path):
    store = MemoryStore(tmp_path / "memory")
    store.add_lessons(None, [
        LessonDraft(condition="git pull 前先 stash", lesson="stash 后 pull"),
    ])
    store.add_lessons("D:/proj/alpha", [
        LessonDraft(condition="alpha 仓库构建用 build.cmd", lesson="build.cmd --fast"),
    ])
    # 仓库级命中且排前(相关性 + repo 加权)
    r = store.recall("alpha 仓库怎么构建", repo_path="D:/proj/alpha")
    assert r.block and "build.cmd" in r.block.splitlines()[1]
    assert len(r.ids) == 1
    # 零命中 → 空块(不注入噪音)
    r2 = store.recall("完全无关的任务文本", repo_path=None)
    assert r2.block == "" and r2.ids == []
    # L0 摘要供 Router/复盘判重
    assert any("build.cmd" in line for line in store.l0_lines("D:/proj/alpha"))


def test_aux_model_used_for_postmortem(make_v2, tmp_path: Path):
    """复盘蒸馏走轻量辅助模型(README「轻量模型」设计意图兑现);主模型不被征用。"""
    mem = tmp_path / "memory"
    main, aux = FakeLLM(), FakeLLM()
    main.set_invoke_responses(list(_TOOL_THEN_DONE))
    aux.set_structured_responses([PostmortemResult(
        lessons=[LessonItem(condition="c", lesson="l", scope="global")])])
    v2 = make_v2({"qwen-max": main, "qwen-turbo": aux}, memory_dir=mem,
                 aux_model_name="qwen-turbo")
    _add_worker(v2)
    resp = v2.scheduler.submit_request("x", worker_name="coder")  # 显式派工 → 不经 Router
    wait_ticket_terminal(v2, resp["ticket_id"])
    _wait_for_event(v2, resp["ticket_id"], ev.POSTMORTEM_COMPLETED)
    assert len(aux.invoked_structured) == 1
    assert main.invoked_structured == []
