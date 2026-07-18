"""DEV_TEAM 配置验证测试。"""
from examples.dev_team import DEV_TEAM
from agentteam.domain.serializer import team_from_dict
from agentteam.domain.team import Team
from agentteam.domain.worker import Worker
from agentteam.domain.mcp_server import MCPServer


def test_dev_team_parses_to_team_dataclass():
    """DEV_TEAM dict 可被 team_from_dict 解析为 Team dataclass。"""
    team = team_from_dict(DEV_TEAM)
    assert isinstance(team, Team)
    assert team.name == "dev_team"
    assert team.description.startswith("研发小队")


def test_dev_team_has_5_roles():
    """研发小队有 1 leader + 4 workers。"""
    team = team_from_dict(DEV_TEAM)
    assert team.leader.name == "tech_lead"
    assert len(team.workers) == 4
    worker_names = [w.name for w in team.workers]
    assert worker_names == ["analyst", "coder", "tester", "reviewer"]


def test_dev_team_leader_has_step_policy():
    """Leader 有 step 级审批策略。"""
    team = team_from_dict(DEV_TEAM)
    assert team.leader.approval_policy is not None
    assert team.leader.approval_policy.level == "step"


def test_dev_team_coder_has_tool_policy():
    """代码工程师有 tool 级审批策略,target 为 write_file。"""
    team = team_from_dict(DEV_TEAM)
    coder = next(w for w in team.workers if w.name == "coder")
    assert coder.approval_policy is not None
    assert coder.approval_policy.level == "tool"
    assert coder.approval_policy.targets == ["write_file"]


def test_dev_team_skills_include_search_web():
    """skills 列表包含 search_web。"""
    team = team_from_dict(DEV_TEAM)
    assert "search_web" in team.skills


def test_dev_team_mcp_git_config():
    """MCP git server 配置正确。"""
    team = team_from_dict(DEV_TEAM)
    assert len(team.mcp_servers) == 1
    server = team.mcp_servers[0]
    assert isinstance(server, MCPServer)
    assert server.name == "git"
    assert server.command == "npx"
    assert "-y" in server.args
    assert "@modelcontextprotocol/server-git" in server.args
    assert server.transport == "stdio"


def test_dev_team_coder_tools_include_mcp_git():
    """代码工程师的工具列表包含 mcp:git: 前缀工具。"""
    team = team_from_dict(DEV_TEAM)
    coder = next(w for w in team.workers if w.name == "coder")
    mcp_tools = [t for t in coder.tools if t.startswith("mcp:git:")]
    assert len(mcp_tools) == 3
    assert "mcp:git:git_status" in coder.tools
    assert "mcp:git:git_diff" in coder.tools
    assert "mcp:git:git_log" in coder.tools


def test_dev_team_can_be_compiled():
    """DEV_TEAM 可被 TeamCompiler 编译为可执行 graph(不运行)。"""
    from langchain_core.tools import StructuredTool
    from tests.conftest import FakeLLM, FakeModelProvider
    from agentteam.runtime.graph import TeamCompiler
    from agentteam.tools.registry import ToolRegistry
    from agentteam.tools.skills import register_builtin_skills

    fake_llm = FakeLLM()
    provider = FakeModelProvider({"qwen-max": fake_llm})

    # fake MCP loader:产出 git_status/git_diff/git_log 三个工具,
    # 避免编译时真实拉起 npx 子进程(default_mcp_loader 会 spawn npx)。
    def _git_status() -> str:
        return "clean"

    def _git_diff() -> str:
        return ""

    def _git_log() -> str:
        return "commit log"

    fake_git_tools = [
        StructuredTool.from_function(name="git_status", description="git status", func=_git_status),
        StructuredTool.from_function(name="git_diff", description="git diff", func=_git_diff),
        StructuredTool.from_function(name="git_log", description="git log", func=_git_log),
    ]
    reg = ToolRegistry(mcp_loader=lambda server: fake_git_tools)
    # 注册原生技能,使 analyst/coder/tester/reviewer 引用的 read_file/write_file/search_web 可解析
    register_builtin_skills(reg)

    compiler = TeamCompiler(provider, reg)
    team = team_from_dict(DEV_TEAM)
    graph = compiler.compile(team)
    node_names = set(graph.get_graph().nodes.keys())
    assert "leader_plan" in node_names
    assert "leader_review" in node_names
    assert "worker_analyst" in node_names
    assert "worker_coder" in node_names
    assert "worker_tester" in node_names
    assert "worker_reviewer" in node_names
    assert "step_gate" in node_names  # Leader 有 step 级策略
