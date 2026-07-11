# tests/runtime/test_sqlite_saver.py
"""SqliteSaver checkpoint 持久化集成测试。"""
from __future__ import annotations

from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from agentteam.domain.team import Leader, Team
from agentteam.domain.worker import Worker
from agentteam.models.provider import ModelRef
from agentteam.runtime.graph import TeamCompiler
from agentteam.runtime.nodes import Plan, PlanStep
from agentteam.runtime.trace import FakeTraceWriter
from agentteam.storage.db import init_db
from agentteam.tools.registry import ToolRegistry
from tests.conftest import FakeLLM, FakeModelProvider


def test_sqlite_saver_persists_checkpoint_across_invocations(tmp_path):
    """SqliteSaver 在 invoke 之间持久化 checkpoint，支持断点续跑。"""
    from agentteam.domain.approval import ApprovalPolicy

    db_path = tmp_path / "test_checkpoint.db"
    conn = init_db(db_path)
    checkpointer = SqliteSaver(conn)
    checkpointer.setup()

    fake_llm = FakeLLM()
    fake_llm.set_structured_responses(
        [Plan(steps=[PlanStep(worker="w1", instruction="do x")])]
    )
    fake_llm.set_invoke_responses(
        [AIMessage(content="done"), AIMessage(content="ok")]
    )

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t",
        description="test",
        leader=Leader(
            name="leader", system_prompt="test",
            approval_policy=ApprovalPolicy(level="step"),
        ),
        workers=[Worker(name="w1", role="r", description="", system_prompt="test")],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    tw = FakeTraceWriter()
    graph = compiler.compile(team, checkpointer=checkpointer, trace_writer=tw)

    config = {"configurable": {"thread_id": "sqlite-test"}}
    initial = {
        "messages": [], "task": "test", "plan": [], "current_step": 0,
        "worker_outputs": {}, "audit_events": [],
        "run_id": "run-sqlite", "pending_approval": None,
    }

    # 第一次 invoke：interrupt
    graph.invoke(initial, config)
    state = graph.get_state(config)
    assert state.next, "应在 step_gate 处暂停"

    # 关闭旧连接，用新连接创建新 checkpointer（模拟跨会话恢复）
    conn.close()
    conn2 = init_db(db_path)
    checkpointer2 = SqliteSaver(conn2)
    checkpointer2.setup()

    # 用新 checkpointer 重新编译图
    graph2 = compiler.compile(team, checkpointer=checkpointer2, trace_writer=tw)

    # Resume：应该能从 checkpoint 恢复
    graph2.invoke(Command(resume={"approved": True, "decider": "tester"}), config)
    state = graph2.get_state(config)
    assert not state.next, "图应该已完成"

    values = state.values
    assert "w1" in values.get("worker_outputs", {})

    conn2.close()


def test_sqlite_saver_no_interrupt_completes(tmp_path):
    """无审批策略时 SqliteSaver 图直接完成。"""
    db_path = tmp_path / "test_nointerrupt.db"
    conn = init_db(db_path)
    checkpointer = SqliteSaver(conn)
    checkpointer.setup()

    fake_llm = FakeLLM()
    fake_llm.set_structured_responses(
        [Plan(steps=[PlanStep(worker="w1", instruction="do x")])]
    )
    fake_llm.set_invoke_responses(
        [AIMessage(content="done"), AIMessage(content="ok")]
    )

    provider = FakeModelProvider({"qwen-max": fake_llm})
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t",
        description="test",
        leader=Leader(name="leader", system_prompt="test"),
        workers=[Worker(name="w1", role="r", description="", system_prompt="test")],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )
    graph = compiler.compile(team, checkpointer=checkpointer)

    config = {"configurable": {"thread_id": "sqlite-nointerrupt"}}
    initial = {
        "messages": [], "task": "test", "plan": [], "current_step": 0,
        "worker_outputs": {}, "audit_events": [],
        "run_id": "run-sqlite2", "pending_approval": None,
    }

    graph.invoke(initial, config)
    state = graph.get_state(config)
    assert not state.next
    assert "w1" in state.values.get("worker_outputs", {})

    conn.close()
