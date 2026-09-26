# tests/runtime/test_sqlite_saver.py
"""SqliteSaver checkpoint 持久化集成测试。"""
from __future__ import annotations

from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from agentteam.domain.team import Leader, Team
from agentteam.domain.worker import Worker
from agentteam.models.provider import ModelRef
from agentteam.runtime.graph import TeamCompiler
from agentteam.runtime.nodes import Plan, PlanStep, ReviewVerdict
from agentteam.storage.db import init_db
from agentteam.tools.registry import ToolRegistry
from tests.conftest import FakeLLM, FakeModelProvider


def test_sqlite_saver_no_interrupt_completes(tmp_path):
    """无审批策略时 SqliteSaver 图直接完成。"""
    db_path = tmp_path / "test_nointerrupt.db"
    conn = init_db(db_path)
    checkpointer = SqliteSaver(conn)
    checkpointer.setup()

    fake_llm = FakeLLM()
    fake_llm.set_structured_responses([
        Plan(steps=[PlanStep(worker="w1", instruction="do x")]),
        ReviewVerdict(passed=True, reason="ok"),
    ])
    fake_llm.set_invoke_responses([AIMessage(content="done")])

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
