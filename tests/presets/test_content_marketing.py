"""content_marketing 预置团队测试。"""
from langchain_core.tools import StructuredTool

from agentteam.domain.agent import Agent
from agentteam.domain.team import Team
from agentteam.runtime.graph import TeamCompiler
from agentteam.tools.registry import ToolRegistry
from agentteam.tools.skills import register_builtin_skills
from tests.conftest import FakeLLM, FakeModelProvider


def _make_fake_mcp_loader():
    """fake mcp_loader:按 server.name 分支返回新工具实例(避免共享引用污染)。

    ToolRegistry.register_mcp_tools 会原地修改 tool.name 加 mcp:{server}:{tool} 前缀,
    若所有 server 共享同一份工具列表,第二次调用会产生双重前缀(mcp:social:mcp:search:query)。
    因此每次调用都新建 StructuredTool 实例。
    """
    def _build_tools(server_name: str):
        if server_name == "search":
            return [
                StructuredTool.from_function(name="query", description="fake", func=lambda: "results"),
                StructuredTool.from_function(name="trends", description="fake", func=lambda: "trends"),
                StructuredTool.from_function(name="keywords", description="fake", func=lambda: "keywords"),
            ]
        elif server_name == "social":
            return [
                StructuredTool.from_function(name="schedule_post", description="fake", func=lambda: "scheduled"),
            ]
        return []

    return lambda server: _build_tools(server.name)


def test_content_marketing_module_exports():
    from agentteam.presets import content_marketing
    assert isinstance(content_marketing.TEAM, Team)
    assert isinstance(content_marketing.LIB_AGENTS, list)
    assert isinstance(content_marketing.METADATA, dict)


def test_content_marketing_metadata_required_keys():
    from agentteam.presets import content_marketing
    meta = content_marketing.METADATA
    for key in ("name", "title", "description", "category", "tags",
                "deps_teams", "deps_library"):
        assert key in meta
    assert meta["name"] == "content_marketing"
    assert meta["category"] == "marketing"


def test_content_marketing_has_two_team_level_mcp():
    """Team 级挂载 2 个 MCP:search + social。"""
    from agentteam.presets import content_marketing
    team = content_marketing.TEAM
    mcp_names = {s.name for s in team.mcp_servers}
    assert {"search", "social"} == mcp_names


def test_content_marketing_has_approval_policies():
    """writer 与 seo 应有 tool 级审批。"""
    from agentteam.presets import content_marketing
    team = content_marketing.TEAM

    def _find_agent(agent, name):
        if agent.name == name:
            return agent
        for child in agent.children:
            if isinstance(child, Agent):
                found = _find_agent(child, name)
                if found:
                    return found
        return None

    writer = _find_agent(team.root, "writer")
    seo = _find_agent(team.root, "seo")
    assert writer is not None and writer.approval_policy is not None
    assert seo is not None and seo.approval_policy is not None


def test_content_marketing_team_compiles():
    from agentteam.presets import content_marketing
    mod = content_marketing
    fake_llm = FakeLLM()
    provider = FakeModelProvider({"qwen-max": fake_llm})
    reg = ToolRegistry(mcp_loader=_make_fake_mcp_loader())
    register_builtin_skills(reg)
    compiler = TeamCompiler(provider, reg)
    graph = compiler.compile(mod.TEAM)
    node_names = set(graph.get_graph().nodes.keys())
    assert "leader_plan" in node_names  # editor_in_chief 是 root supervisor
    assert "worker_planner" in node_names
    assert "worker_writer" in node_names
    assert "worker_seo" in node_names
