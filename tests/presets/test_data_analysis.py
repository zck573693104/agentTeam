"""data_analysis 预置团队测试。"""
from langchain_core.tools import StructuredTool

from agentteam.domain.agent import Agent
from agentteam.domain.team import Team
from agentteam.runtime.graph import TeamCompiler
from agentteam.tools.registry import ToolRegistry
from agentteam.tools.skills import register_builtin_skills
from tests.conftest import FakeLLM, FakeModelProvider


def _make_fake_mcp_loader():
    """fake mcp_loader:按 server.name 产出 db 或 chart 工具。

    每次调用都新建工具实例,避免 register_mcp_tools 修改 tool.name 时
    跨 server 共享引用导致前缀叠加。
    """
    def _query() -> str:
        return "rows"

    def _schema() -> str:
        return "schema"

    def _render() -> str:
        return "chart"

    def _export() -> str:
        return "exported"

    def _loader(server):
        if server.name == "db":
            return [
                StructuredTool.from_function(name="query", description="fake query", func=_query),
                StructuredTool.from_function(name="schema", description="fake schema", func=_schema),
            ]
        if server.name == "chart":
            return [
                StructuredTool.from_function(name="render", description="fake render", func=_render),
                StructuredTool.from_function(name="export", description="fake export", func=_export),
            ]
        return []
    return _loader


def test_data_analysis_module_exports():
    from agentteam.presets import data_analysis
    assert isinstance(data_analysis.TEAM, Team)
    assert isinstance(data_analysis.LIB_AGENTS, list)
    assert isinstance(data_analysis.METADATA, dict)


def test_data_analysis_metadata_required_keys():
    from agentteam.presets import data_analysis
    meta = data_analysis.METADATA
    for key in ("name", "title", "description", "category", "tags",
                "deps_teams", "deps_library"):
        assert key in meta
    assert meta["name"] == "data_analysis"
    assert meta["category"] == "analytics"


def test_data_analysis_has_two_team_level_mcp():
    """Team 级挂载 2 个 MCP:db + chart。"""
    from agentteam.presets import data_analysis
    team = data_analysis.TEAM
    mcp_names = {s.name for s in team.mcp_servers}
    assert {"db", "chart"} == mcp_names


def test_data_analysis_no_approval_policy():
    """数据分析无破坏性操作,不应有审批策略。"""
    from agentteam.presets import data_analysis
    team = data_analysis.TEAM

    def _has_approval(agent):
        if agent.approval_policy is not None:
            return True
        for child in agent.children:
            if isinstance(child, Agent) and _has_approval(child):
                return True
        return False
    assert not _has_approval(team.root), "data_analysis 不应有审批策略"


def test_data_analysis_team_compiles():
    from agentteam.presets import data_analysis
    mod = data_analysis
    fake_llm = FakeLLM()
    provider = FakeModelProvider({"qwen-max": fake_llm})
    reg = ToolRegistry(mcp_loader=_make_fake_mcp_loader())
    register_builtin_skills(reg)
    compiler = TeamCompiler(provider, reg)
    graph = compiler.compile(mod.TEAM)
    node_names = set(graph.get_graph().nodes.keys())
    assert "leader_plan" in node_names  # analytics_director 是 root supervisor
    assert "worker_sql_engineer" in node_names
    assert "worker_visualizer" in node_names
