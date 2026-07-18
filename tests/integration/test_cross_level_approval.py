"""跨层审批集成测试。"""
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agentteam.domain.agent import Agent
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef
from agentteam.runtime.graph import TeamCompiler
from agentteam.runtime.nodes import Plan, PlanStep
from agentteam.tools.registry import ToolRegistry
from tests.conftest import FakeLLM, FakeModelProvider


def _initial_state(task="t", run_id="r1"):
    return {
        "messages": [], "task": task, "plan": [], "current_step": 0,
        "worker_outputs": {}, "audit_events": [], "run_id": run_id,
        "pending_approval": None, "total_tokens": 0, "path": "team:t",
    }


def test_cross_level_step_and_tool_approval(fake_trace_writer, tmp_path):
    """父层 step 级审批 + 子 worker tool 级审批，分别 interrupt 与 resume。"""
    # 父 leader LLM：拆 1 步给 child
    parent_llm = FakeLLM()
    parent_llm.set_structured_responses([Plan(steps=[
        PlanStep(worker="child", instruction="do work"),
    ])])
    parent_llm.set_invoke_responses([AIMessage(content="child done")])

    # child worker LLM：先调工具，再给答案
    child_llm = FakeLLM()
    child_llm.set_invoke_responses([
        AIMessage(
            content="",
            tool_calls=[{"name": "write_file", "args": {"content": "x"},
                         "id": "tc1", "type": "tool_call"}],
        ),
        AIMessage(content="work done"),
    ])

    # write_file 工具
    target = tmp_path / "out.txt"
    def write_file(content: str) -> str:
        target.write_text(content, encoding="utf-8")
        return "written"
    tool = StructuredTool.from_function(
        name="write_file", description="write", func=write_file,
    )
    reg = ToolRegistry()
    reg.register(tool)

    provider = FakeModelProvider({
        "parent-model": parent_llm, "child-model": child_llm,
    })
    compiler = TeamCompiler(provider, reg)
    team = Team(
        name="t", description="cross level",
        root=Agent(
            name="parent", role="supervisor", system_prompt="parent",
            model=ModelRef("qwen", "parent-model"),
            approval_policy=ApprovalPolicy(level="step"),  # 父层 step 级
            children=[Agent(
                name="child", role="worker", system_prompt="child",
                model=ModelRef("qwen", "child-model"),
                tools=["write_file"],
                approval_policy=ApprovalPolicy(level="tool", targets=["write_file"]),
            )],
        ),
        default_model=ModelRef("qwen", "parent-model"),
    )
    graph = compiler.compile(
        team, checkpointer=MemorySaver(), trace_writer=fake_trace_writer,
    )
    config = {"configurable": {"thread_id": "cross"}}

    # 第一次 invoke：应在父 step_gate 处 interrupt
    graph.invoke(_initial_state(), config)
    state = graph.get_state(config)
    assert state.next, "应在父层 step_gate 处 interrupt"

    # Resume 父层审批：批准
    graph.invoke(Command(resume={"approved": True, "decider": "user"}), config)
    state = graph.get_state(config)
    assert state.next, "应在子层 tool_step 处 interrupt"

    # Resume 子层审批：批准
    graph.invoke(Command(resume={"approved": True, "decider": "user"}), config)
    state = graph.get_state(config)
    assert not state.next, "图应该已完成"

    # 验证工具被执行
    assert target.read_text(encoding="utf-8") == "x"

    # 验证两层审批事件都有
    event_types = [e["event_type"] for e in fake_trace_writer.events]
    approval_requests = [i for i, t in enumerate(event_types) if t == "approval_requested"]
    approval_decideds = [i for i, t in enumerate(event_types) if t == "approval_decided"]
    assert len(approval_requests) == 2  # 父 step + 子 tool
    assert len(approval_decideds) == 2

    # 验证 worker 产出
    assert state.values["worker_outputs"].get("child") == "work done"


def test_cross_level_step_rejection_terminates(fake_trace_writer):
    """父层 step 级审批拒绝 → 图终止，子层不执行。"""
    parent_llm = FakeLLM()
    parent_llm.set_structured_responses([Plan(steps=[
        PlanStep(worker="child", instruction="do work"),
    ])])
    # 不会用到 review，因为拒绝后终止

    child_llm = FakeLLM()
    child_llm.set_invoke_responses([AIMessage(content="should not run")])

    provider = FakeModelProvider({
        "parent-model": parent_llm, "child-model": child_llm,
    })
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t", description="reject",
        root=Agent(
            name="parent", role="supervisor", system_prompt="parent",
            model=ModelRef("qwen", "parent-model"),
            approval_policy=ApprovalPolicy(level="step"),
            children=[Agent(
                name="child", role="worker", system_prompt="child",
                model=ModelRef("qwen", "child-model"),
            )],
        ),
        default_model=ModelRef("qwen", "parent-model"),
    )
    graph = compiler.compile(
        team, checkpointer=MemorySaver(), trace_writer=fake_trace_writer,
    )
    config = {"configurable": {"thread_id": "reject"}}

    # 第一次 invoke：interrupt
    graph.invoke(_initial_state(), config)
    state = graph.get_state(config)
    assert state.next

    # Resume：拒绝
    graph.invoke(Command(resume={"approved": False, "decider": "user"}), config)
    state = graph.get_state(config)
    assert not state.next, "图应已终止"

    # worker 未执行
    assert "child" not in state.values.get("worker_outputs", {})
