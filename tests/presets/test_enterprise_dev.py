"""enterprise_dev 预置团队测试。"""
from langchain_core.tools import StructuredTool

from agentteam.domain.agent import Agent, TeamRef
from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.library import AgentLibrary
from agentteam.domain.mcp_server import MCPServer
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef
from agentteam.runtime.graph import TeamCompiler
from agentteam.tools.registry import ToolRegistry
from agentteam.tools.skills import register_builtin_skills
from tests.conftest import FakeLLM, FakeModelProvider


def _make_fake_mcp_loader():
    """构造 fake mcp_loader,产出 git_status/git_diff/git_log 工具。"""
    def _git_status() -> str:
        return "clean"

    def _git_diff() -> str:
        return ""

    def _git_log() -> str:
        return "commit log"

    fake_tools = [
        StructuredTool.from_function(name="git_status", description="git status", func=_git_status),
        StructuredTool.from_function(name="git_diff", description="git diff", func=_git_diff),
        StructuredTool.from_function(name="git_log", description="git log", func=_git_log),
    ]
    return lambda server: fake_tools


def test_enterprise_dev_module_exports():
    """enterprise_dev 模块导出 TEAM/LIB_AGENTS/METADATA/TEST_SUBTEAM。"""
    from agentteam.presets import enterprise_dev
    assert isinstance(enterprise_dev.TEAM, Team)
    assert isinstance(enterprise_dev.LIB_AGENTS, list)
    assert isinstance(enterprise_dev.METADATA, dict)
    assert isinstance(enterprise_dev.TEST_SUBTEAM, Team)


def test_enterprise_dev_metadata_required_keys():
    """METADATA 包含所有必需 keys。"""
    from agentteam.presets import enterprise_dev
    meta = enterprise_dev.METADATA
    for key in ("name", "title", "description", "category", "tags",
                "deps_teams", "deps_library"):
        assert key in meta, f"METADATA 缺少 key: {key}"
    assert meta["name"] == "enterprise_dev"
    assert meta["category"] == "research"


def test_enterprise_dev_lib_agents_match_metadata():
    """LIB_AGENTS 中每个 agent.name 出现在 deps_library 中。"""
    from agentteam.presets import enterprise_dev
    meta = enterprise_dev.METADATA
    lib_names = {a.name for a in enterprise_dev.LIB_AGENTS}
    assert lib_names == set(meta["deps_library"]), \
        f"LIB_AGENTS names {lib_names} != deps_library {meta['deps_library']}"


def test_enterprise_dev_deps_teams_variable_exists():
    """deps_teams 中每个 name 在模块中有对应 Team 变量(大写优先)。"""
    from agentteam.presets import enterprise_dev
    for team_name in enterprise_dev.METADATA["deps_teams"]:
        var_upper = team_name.upper()
        var_orig = team_name
        sub_team = getattr(enterprise_dev, var_upper, None) or getattr(enterprise_dev, var_orig, None)
        assert sub_team is not None, \
            f"deps_teams 声明 {team_name!r} 但模块未定义 {var_upper!r} 或 {var_orig!r}"
        assert isinstance(sub_team, Team)


def test_enterprise_dev_has_mcp():
    """至少 1 个 MCP 挂载(Team 级或 Agent 级)。"""
    from agentteam.presets import enterprise_dev
    team = enterprise_dev.TEAM
    has_team_mcp = len(team.mcp_servers) > 0
    # 递归检查 agent 级 MCP
    def _has_agent_mcp(agent):
        if agent.mcp_servers:
            return True
        for child in agent.children:
            if isinstance(child, Agent) and _has_agent_mcp(child):
                return True
        return False
    assert has_team_mcp or _has_agent_mcp(team.root), "enterprise_dev 应至少挂载 1 个 MCP"


def test_enterprise_dev_has_approval_policy():
    """至少 1 个声明式审批策略。"""
    from agentteam.presets import enterprise_dev
    team = enterprise_dev.TEAM

    def _has_approval(agent):
        if agent.approval_policy is not None:
            return True
        for child in agent.children:
            if isinstance(child, Agent) and _has_approval(child):
                return True
        return False
    assert _has_approval(team.root), "enterprise_dev 应至少有 1 个 approval_policy"


def test_enterprise_dev_team_compiles():
    """TEAM 可被 TeamCompiler 成功编译(注册 library + sub-team 后)。"""
    from agentteam.presets import enterprise_dev
    mod = enterprise_dev

    fake_llm = FakeLLM()
    provider = FakeModelProvider({"qwen-max": fake_llm})
    reg = ToolRegistry(mcp_loader=_make_fake_mcp_loader())
    register_builtin_skills(reg)

    # 注册专家库
    lib = AgentLibrary()
    for a in mod.LIB_AGENTS:
        lib.register(a)

    compiler = TeamCompiler(provider, reg, library=lib)
    # 注册 sub-team(供 TeamRef 解析)
    for team_name in mod.METADATA["deps_teams"]:
        sub_team = getattr(mod, team_name.upper(), None) or getattr(mod, team_name)
        compiler.register_team(sub_team)

    # 编译主 TEAM(不 invoke)
    graph = compiler.compile(mod.TEAM)
    node_names = set(graph.get_graph().nodes.keys())
    # CEO 是 root supervisor,应有 leader_plan 节点
    assert "leader_plan" in node_names
    # CTO 是 CEO 的子 supervisor,外层图以 agent_cto 节点呈现
    # (eng/reviewer 嵌套在 CTO 子图内,不暴露到外层图)
    assert "agent_cto" in node_names


def test_enterprise_dev_subteam_compiles():
    """TEST_SUBTEAM 可独立编译。"""
    from agentteam.presets import enterprise_dev
    fake_llm = FakeLLM()
    provider = FakeModelProvider({"qwen-max": fake_llm})
    reg = ToolRegistry(mcp_loader=_make_fake_mcp_loader())
    register_builtin_skills(reg)
    compiler = TeamCompiler(provider, reg)
    graph = compiler.compile(enterprise_dev.TEST_SUBTEAM)
    node_names = set(graph.get_graph().nodes.keys())
    assert "worker_tester" in node_names
