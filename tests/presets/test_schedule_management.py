"""schedule_management 预置团队测试。"""
from langchain_core.tools import StructuredTool

from agentteam.domain.agent import Agent
from agentteam.domain.team import Team
from agentteam.runtime.graph import TeamCompiler
from agentteam.tools.registry import ToolRegistry
from agentteam.tools.skills import register_builtin_skills
from tests.conftest import FakeLLM, FakeModelProvider


def _make_fake_mcp_loader():
    """fake mcp_loader:产出 calendar 工具。

    每次调用新建工具实例,避免 register_mcp_tools 修改 tool.name 时
    跨 server 共享引用导致前缀叠加。
    """
    def _find_slots() -> str:
        return "slots"

    def _create_event() -> str:
        return "created"

    def _get_schedule() -> str:
        return "schedule"

    def _set_reminder() -> str:
        return "reminder set"

    def _loader(server):
        if server.name == "calendar":
            return [
                StructuredTool.from_function(name="find_slots", description="fake", func=_find_slots),
                StructuredTool.from_function(name="create_event", description="fake", func=_create_event),
                StructuredTool.from_function(name="get_schedule", description="fake", func=_get_schedule),
                StructuredTool.from_function(name="set_reminder", description="fake", func=_set_reminder),
            ]
        return []
    return _loader


def test_schedule_management_module_exports():
    from agentteam.presets import schedule_management
    assert isinstance(schedule_management.TEAM, Team)
    assert isinstance(schedule_management.LIB_AGENTS, list)
    assert isinstance(schedule_management.METADATA, dict)


def test_schedule_management_metadata_required_keys():
    from agentteam.presets import schedule_management
    meta = schedule_management.METADATA
    for key in ("name", "title", "description", "category", "tags",
                "deps_teams", "deps_library"):
        assert key in meta
    assert meta["name"] == "schedule_management"
    assert meta["category"] == "productivity"


def test_schedule_management_has_calendar_mcp():
    """Team 级挂载 calendar MCP。"""
    from agentteam.presets import schedule_management
    team = schedule_management.TEAM
    mcp_names = {s.name for s in team.mcp_servers}
    assert {"calendar"} == mcp_names


def test_schedule_management_has_approval_policy():
    """planner 应有 tool 级审批(create_event)。"""
    from agentteam.presets import schedule_management
    team = schedule_management.TEAM

    def _find_agent(agent, name):
        if agent.name == name:
            return agent
        for child in agent.children:
            if isinstance(child, Agent):
                found = _find_agent(child, name)
                if found:
                    return found
        return None

    planner = _find_agent(team.root, "planner")
    assert planner is not None and planner.approval_policy is not None
    assert "mcp:calendar:create_event" in planner.approval_policy.targets


def test_schedule_management_reminder_no_approval():
    """reminder 无破坏性操作,不应有审批策略。"""
    from agentteam.presets import schedule_management
    team = schedule_management.TEAM

    def _find_agent(agent, name):
        if agent.name == name:
            return agent
        for child in agent.children:
            if isinstance(child, Agent):
                found = _find_agent(child, name)
                if found:
                    return found
        return None

    reminder = _find_agent(team.root, "reminder")
    assert reminder is not None and reminder.approval_policy is None


def test_schedule_management_team_compiles():
    from agentteam.presets import schedule_management
    mod = schedule_management
    fake_llm = FakeLLM()
    provider = FakeModelProvider({"qwen-max": fake_llm})
    reg = ToolRegistry(mcp_loader=_make_fake_mcp_loader())
    register_builtin_skills(reg)
    compiler = TeamCompiler(provider, reg)
    graph = compiler.compile(mod.TEAM)
    node_names = set(graph.get_graph().nodes.keys())
    assert "leader_plan" in node_names  # schedule_manager 是 root supervisor
    assert "worker_planner" in node_names
    assert "worker_reminder" in node_names
    assert "worker_prioritizer" in node_names
