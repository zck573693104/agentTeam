"""多级 MCP 挂载 E2E 测试。"""
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver

from agentteam.domain.agent import Agent, TeamRef
from agentteam.domain.mcp_server import MCPServer
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef
from agentteam.runtime.graph import TeamCompiler
from agentteam.runtime.nodes import Plan, PlanStep
from agentteam.tools.registry import ToolRegistry
from tests.conftest import FakeLLM, FakeModelProvider


def _make_mcp_loader(tool_name: str, response: str = "ok"):
    """构造 fake mcp_loader，返回单个工具。"""
    def loader(server):
        return [StructuredTool.from_function(
            name=tool_name, description=f"fake {tool_name}",
            func=lambda **kwargs: response,
        )]
    return loader


def _initial_state(task="t", run_id="r1"):
    return {
        "messages": [], "task": task, "plan": [], "current_step": 0,
        "worker_outputs": {}, "audit_events": [], "run_id": run_id,
        "pending_approval": None, "total_tokens": 0, "path": "team:t",
    }


def test_e2e_worker_level_mcp():
    """Worker 级 MCP：coder 挂载 git MCP，调用 git_status 工具。"""
    leader_llm = FakeLLM()
    leader_llm.set_structured_responses([Plan(steps=[
        PlanStep(worker="coder", instruction="check git status"),
    ])])
    leader_llm.set_invoke_responses([AIMessage(content="coder done")])

    coder_llm = FakeLLM()
    coder_llm.set_invoke_responses([
        AIMessage(
            content="",
            tool_calls=[{"name": "mcp:git:git_status", "args": {},
                         "id": "tc1", "type": "tool_call"}],
        ),
        AIMessage(content="git status: clean"),
    ])

    reg = ToolRegistry(mcp_loader=_make_mcp_loader("git_status", "clean"))
    provider = FakeModelProvider({
        "leader-model": leader_llm, "coder-model": coder_llm,
    })
    compiler = TeamCompiler(provider, reg)
    team = Team(
        name="t", description="worker mcp",
        root=Agent(
            name="lead", role="supervisor", system_prompt="lead",
            model=ModelRef("qwen", "leader-model"),
            children=[Agent(
                name="coder", role="worker", system_prompt="coder",
                model=ModelRef("qwen", "coder-model"),
                mcp_servers=[MCPServer(name="git", command="git-mcp")],
                tools=["mcp:git:git_status"],
            )],
        ),
        default_model=ModelRef("qwen", "leader-model"),
    )
    graph = compiler.compile(team, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "wmcp"}}
    graph.invoke(_initial_state(), config)

    state = graph.get_state(config)
    assert not state.next
    assert state.values["worker_outputs"]["coder"] == "git status: clean"


def test_e2e_teamref_mcp_overrides():
    """TeamRef 级 MCP 覆盖：父 Team 引用 sub-Team 时追加 MCP 服务。"""
    parent_llm = FakeLLM()
    parent_llm.set_structured_responses([Plan(steps=[
        PlanStep(worker="qa", instruction="run extra tool"),
    ])])
    parent_llm.set_invoke_responses([AIMessage(content="qa done")])

    sub_llm = FakeLLM()
    sub_llm.set_structured_responses([Plan(steps=[
        PlanStep(worker="tester", instruction="test"),
    ])])
    sub_llm.set_invoke_responses([AIMessage(content="sub done")])

    tester_llm = FakeLLM()
    tester_llm.set_invoke_responses([
        AIMessage(
            content="",
            tool_calls=[{"name": "mcp:extra:extra_tool", "args": {},
                         "id": "tc1", "type": "tool_call"}],
        ),
        AIMessage(content="extra result"),
    ])

    def fake_loader(server):
        if server.name == "extra":
            return [StructuredTool.from_function(
                name="extra_tool", description="extra", func=lambda **k: "extra-ok")]
        return []

    reg = ToolRegistry(mcp_loader=fake_loader)
    provider = FakeModelProvider({
        "parent-model": parent_llm,
        "sub-model": sub_llm,
        "tester-model": tester_llm,
    })
    compiler = TeamCompiler(provider, reg)

    sub_team = Team(
        name="sub", description="sub",
        root=Agent(
            name="sub_lead", role="supervisor", system_prompt="sub",
            model=ModelRef("qwen", "sub-model"),
            children=[Agent(
                name="tester", role="worker", system_prompt="tester",
                model=ModelRef("qwen", "tester-model"),
                tools=["mcp:extra:extra_tool"],
            )],
        ),
        default_model=ModelRef("qwen", "sub-model"),
    )
    compiler.register_team(sub_team)

    main_team = Team(
        name="main", description="teamref mcp",
        root=Agent(
            name="lead", role="supervisor", system_prompt="main",
            model=ModelRef("qwen", "parent-model"),
            children=[TeamRef(
                name="sub", alias="qa",
                mcp_overrides=[MCPServer(name="extra", command="extra-mcp")],
            )],
        ),
        default_model=ModelRef("qwen", "parent-model"),
    )
    graph = compiler.compile(main_team, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "tmcp"}}
    graph.invoke(_initial_state(), config)

    state = graph.get_state(config)
    assert not state.next
    assert state.values["worker_outputs"]["tester"] == "extra result"
