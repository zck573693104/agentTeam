import time

from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from agentteam.api.events import EventBus
from agentteam.api.run_manager import RunManager
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.team import Leader, Team
from agentteam.domain.worker import Worker
from agentteam.models.provider import ModelRef
from agentteam.runtime.graph import TeamCompiler
from agentteam.runtime.nodes import Plan, PlanStep
from agentteam.storage.audit import AuditRepo
from agentteam.storage.db import init_db
from agentteam.storage.runs import RunRepo
from agentteam.tools.registry import ToolRegistry
from tests.conftest import FakeLLM, FakeModelProvider


def _wait_for_status(run_repo, run_id, timeout=10.0, target_statuses=None):
    """轮询 run 状态直到非 running/pending 或匹配目标状态。"""
    target = target_statuses or {"completed", "failed", "interrupted"}
    for _ in range(int(timeout * 10)):
        run = run_repo.get_run(run_id)
        if run and run["status"] in target:
            return run["status"]
        time.sleep(0.1)
    return None


def _get_event(q, event_type, timeout=5.0):
    """从队列中取 event_type 类型的事件（跳过前面的其他事件）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            event = q.get(timeout=remaining)
        except Exception:
            return None
        if event.get("event_type") == event_type:
            return event
    return None


def _make_team_with_approval() -> Team:
    return Team(
        name="t",
        description="test",
        leader=Leader(
            system_prompt="test",
            approval_policy=ApprovalPolicy(level="step"),
        ),
        workers=[Worker(name="w1", role="r", description="", system_prompt="test")],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )


def _make_team_no_approval() -> Team:
    return Team(
        name="t",
        description="test",
        leader=Leader(system_prompt="test"),
        workers=[Worker(name="w1", role="r", description="", system_prompt="test")],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )


def _compile_graph(team, fake_llm, conn, trace_writer=None):
    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    saver = SqliteSaver(conn)
    saver.setup()
    return compiler.compile(team, checkpointer=saver, trace_writer=trace_writer)


def test_start_run_completes_without_approval(tmp_path):
    conn = init_db(tmp_path / "test.db")
    run_repo = RunRepo(conn)
    audit_repo = AuditRepo(conn)
    bus = EventBus()
    rm = RunManager(run_repo, audit_repo, bus)

    fake_llm = FakeLLM()
    fake_llm.set_structured_responses([Plan(steps=[PlanStep(worker="w1", instruction="do x")])])
    fake_llm.set_invoke_responses([AIMessage(content="done"), AIMessage(content="ok")])

    team = _make_team_no_approval()
    graph = _compile_graph(team, fake_llm, conn)
    run_id = run_repo.create_run("t", "test task")
    config = {"configurable": {"thread_id": run_id}}

    rm.start_run(run_id, graph, config, "test task")
    status = _wait_for_status(run_repo, run_id)
    assert status == "completed"
    conn.close()


def test_start_run_interrupts_with_step_approval(tmp_path):
    conn = init_db(tmp_path / "test.db")
    run_repo = RunRepo(conn)
    audit_repo = AuditRepo(conn)
    bus = EventBus()
    rm = RunManager(run_repo, audit_repo, bus)

    fake_llm = FakeLLM()
    fake_llm.set_structured_responses([Plan(steps=[PlanStep(worker="w1", instruction="do x")])])

    team = _make_team_with_approval()
    graph = _compile_graph(team, fake_llm, conn)
    run_id = run_repo.create_run("t", "test task")
    config = {"configurable": {"thread_id": run_id}}
    q = bus.subscribe(run_id)  # 必须用实际 run_id 订阅

    rm.start_run(run_id, graph, config, "test task")
    status = _wait_for_status(run_repo, run_id)
    assert status == "interrupted"

    # run_interrupted 事件推到了 EventBus（跳过 run_start）
    event = _get_event(q, "run_interrupted")
    assert event is not None
    assert event["event_type"] == "run_interrupted"
    conn.close()


def test_resume_run_completes_after_interrupt(tmp_path):
    conn = init_db(tmp_path / "test.db")
    run_repo = RunRepo(conn)
    audit_repo = AuditRepo(conn)
    bus = EventBus()
    rm = RunManager(run_repo, audit_repo, bus)

    fake_llm = FakeLLM()
    fake_llm.set_structured_responses([Plan(steps=[PlanStep(worker="w1", instruction="do x")])])
    fake_llm.set_invoke_responses([AIMessage(content="done"), AIMessage(content="ok")])

    team = _make_team_with_approval()
    graph = _compile_graph(team, fake_llm, conn)
    run_id = run_repo.create_run("t", "test task")
    config = {"configurable": {"thread_id": run_id}}

    rm.start_run(run_id, graph, config, "test task")
    _wait_for_status(run_repo, run_id)

    # resume
    rm.resume_run(run_id, approved=True, reason="ok")
    status = _wait_for_status(run_repo, run_id)
    assert status == "completed"
    conn.close()


def test_start_run_handles_error(tmp_path):
    conn = init_db(tmp_path / "test.db")
    run_repo = RunRepo(conn)
    audit_repo = AuditRepo(conn)
    bus = EventBus()
    rm = RunManager(run_repo, audit_repo, bus)

    # FakeLLM 不设 responses → invoke 时 IndexError
    fake_llm = FakeLLM()
    team = _make_team_no_approval()
    graph = _compile_graph(team, fake_llm, conn)
    run_id = run_repo.create_run("t", "test task")
    config = {"configurable": {"thread_id": run_id}}
    q = bus.subscribe(run_id)  # 必须用实际 run_id 订阅

    rm.start_run(run_id, graph, config, "test task")
    status = _wait_for_status(run_repo, run_id)
    assert status == "failed"

    # error 事件推到了 EventBus（跳过 run_start）
    event = _get_event(q, "error")
    assert event is not None
    assert event["event_type"] == "error"
    conn.close()


def test_cleanup_after_run_completes(tmp_path):
    """run 完成后 _graphs/_configs/_threads 应被清理，防止内存泄漏。"""
    conn = init_db(tmp_path / "test.db")
    run_repo = RunRepo(conn)
    audit_repo = AuditRepo(conn)
    bus = EventBus()
    rm = RunManager(run_repo, audit_repo, bus)

    fake_llm = FakeLLM()
    fake_llm.set_structured_responses([Plan(steps=[PlanStep(worker="w1", instruction="do x")])])
    fake_llm.set_invoke_responses([AIMessage(content="done"), AIMessage(content="ok")])

    team = _make_team_no_approval()
    graph = _compile_graph(team, fake_llm, conn)
    run_id = run_repo.create_run("t", "test task")
    config = {"configurable": {"thread_id": run_id}}

    rm.start_run(run_id, graph, config, "test task")
    _wait_for_status(run_repo, run_id)

    # 等待后台线程完成清理
    time.sleep(0.3)
    assert run_id not in rm._graphs
    assert run_id not in rm._configs
    assert run_id not in rm._threads
    conn.close()


def test_cleanup_after_run_fails(tmp_path):
    """run 失败后 _graphs/_configs/_threads 应被清理。"""
    conn = init_db(tmp_path / "test.db")
    run_repo = RunRepo(conn)
    audit_repo = AuditRepo(conn)
    bus = EventBus()
    rm = RunManager(run_repo, audit_repo, bus)

    fake_llm = FakeLLM()  # 不设 responses → 触发异常
    team = _make_team_no_approval()
    graph = _compile_graph(team, fake_llm, conn)
    run_id = run_repo.create_run("t", "test task")
    config = {"configurable": {"thread_id": run_id}}

    rm.start_run(run_id, graph, config, "test task")
    _wait_for_status(run_repo, run_id, target_statuses={"failed"})

    time.sleep(0.3)
    assert run_id not in rm._graphs
    assert run_id not in rm._configs
    assert run_id not in rm._threads
    conn.close()


def test_no_cleanup_after_run_interrupted(tmp_path):
    """run 中断后 _graphs/_configs 应保留，供 resume 使用。"""
    conn = init_db(tmp_path / "test.db")
    run_repo = RunRepo(conn)
    audit_repo = AuditRepo(conn)
    bus = EventBus()
    rm = RunManager(run_repo, audit_repo, bus)

    fake_llm = FakeLLM()
    fake_llm.set_structured_responses([Plan(steps=[PlanStep(worker="w1", instruction="do x")])])

    team = _make_team_with_approval()
    graph = _compile_graph(team, fake_llm, conn)
    run_id = run_repo.create_run("t", "test task")
    config = {"configurable": {"thread_id": run_id}}

    rm.start_run(run_id, graph, config, "test task")
    _wait_for_status(run_repo, run_id, target_statuses={"interrupted"})

    time.sleep(0.3)
    # interrupted 的 run 不清理——resume 需要 graph/config
    assert run_id in rm._graphs
    assert run_id in rm._configs
    conn.close()
