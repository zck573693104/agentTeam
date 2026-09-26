"""personal_assistant 预置团队测试。"""
from langchain_core.tools import StructuredTool

from agentteam.domain.agent import Agent
from agentteam.domain.team import Team
from agentteam.runtime.graph import TeamCompiler
from agentteam.tools.registry import ToolRegistry
from agentteam.tools.skills import register_builtin_skills
from tests.conftest import FakeLLM, FakeModelProvider


def _make_fake_mcp_loader():
    """fake mcp_loader:按 server.name 产出 search/calendar/email 工具。"""
    def _build_tools(server_name: str):
        if server_name == "search":
            return [
                StructuredTool.from_function(name="query", description="fake", func=lambda: "results"),
                StructuredTool.from_function(name="trends", description="fake", func=lambda: "trends"),
            ]
        if server_name == "calendar":
            return [
                StructuredTool.from_function(name="find_slots", description="fake", func=lambda: "slots"),
                StructuredTool.from_function(name="create_event", description="fake", func=lambda: "created"),
                StructuredTool.from_function(name="get_schedule", description="fake", func=lambda: "schedule"),
            ]
        if server_name == "email":
            return [
                StructuredTool.from_function(name="send_email", description="fake", func=lambda: "sent"),
                StructuredTool.from_function(name="list_inbox", description="fake", func=lambda: "[]"),
            ]
        return []
    return lambda server: _build_tools(server.name)


def test_personal_assistant_module_exports():
    from agentteam.presets import personal_assistant
    assert isinstance(personal_assistant.TEAM, Team)
    assert isinstance(personal_assistant.LIB_AGENTS, list)
    assert isinstance(personal_assistant.METADATA, dict)


def test_personal_assistant_metadata_required_keys():
    from agentteam.presets import personal_assistant
    meta = personal_assistant.METADATA
    for key in ("name", "title", "description", "category", "tags",
                "deps_teams", "deps_library"):
        assert key in meta
    assert meta["name"] == "personal_assistant"
    assert meta["category"] == "productivity"


def test_personal_assistant_has_three_team_level_mcp():
    """Team 级挂载 3 个 MCP:search + calendar + email。"""
    from agentteam.presets import personal_assistant
    team = personal_assistant.TEAM
    mcp_names = {s.name for s in team.mcp_servers}
    assert {"search", "calendar", "email"} == mcp_names


def test_personal_assistant_lib_agents_empty():
    """个人助理团队无专家库依赖。"""
    from agentteam.presets import personal_assistant
    assert personal_assistant.LIB_AGENTS == []
    assert personal_assistant.METADATA["deps_library"] == []


def test_personal_assistant_has_approval_policies():
    """schedule_coordinator 与 notifier 应有 tool 级审批。"""
    from agentteam.presets import personal_assistant
    team = personal_assistant.TEAM

    def _find_agent(agent, name):
        if agent.name == name:
            return agent
        for child in agent.children:
            if isinstance(child, Agent):
                found = _find_agent(child, name)
                if found:
                    return found
        return None

    coordinator = _find_agent(team.root, "schedule_coordinator")
    notifier = _find_agent(team.root, "notifier")
    assert coordinator is not None and coordinator.approval_policy is not None
    assert "mcp:calendar:create_event" in coordinator.approval_policy.targets
    assert notifier is not None and notifier.approval_policy is not None
    assert "mcp:email:send_email" in notifier.approval_policy.targets


def test_personal_assistant_info_collector_no_approval():
    """info_collector 只读,不应有审批策略。"""
    from agentteam.presets import personal_assistant
    team = personal_assistant.TEAM

    def _find_agent(agent, name):
        if agent.name == name:
            return agent
        for child in agent.children:
            if isinstance(child, Agent):
                found = _find_agent(child, name)
                if found:
                    return found
        return None

    info_collector = _find_agent(team.root, "info_collector")
    assert info_collector is not None and info_collector.approval_policy is None


def test_personal_assistant_team_compiles():
    from agentteam.presets import personal_assistant
    mod = personal_assistant
    fake_llm = FakeLLM()
    provider = FakeModelProvider({"qwen-max": fake_llm})
    reg = ToolRegistry(mcp_loader=_make_fake_mcp_loader())
    register_builtin_skills(reg)
    compiler = TeamCompiler(provider, reg)
    graph = compiler.compile(mod.TEAM)
    node_names = set(graph.get_graph().nodes.keys())
    assert "leader_plan" in node_names  # assistant 是 root supervisor
    assert "worker_info_collector" in node_names
    assert "worker_schedule_coordinator" in node_names
    assert "worker_notifier" in node_names
