"""project_management 预置团队测试。"""
from langchain_core.tools import StructuredTool

from agentteam.domain.agent import Agent
from agentteam.domain.library import AgentLibrary
from agentteam.domain.team import Team
from agentteam.runtime.graph import TeamCompiler
from agentteam.tools.registry import ToolRegistry
from agentteam.tools.skills import register_builtin_skills
from tests.conftest import FakeLLM, FakeModelProvider


def _make_fake_mcp_loader():
    """fake mcp_loader:产出 task 工具。"""
    def _create_task() -> str:
        return "task created"

    def _create_subtask() -> str:
        return "subtask created"

    def _list_tasks() -> str:
        return "[]"

    def _update_status() -> str:
        return "updated"

    def _get_task() -> str:
        return "{}"

    def _loader(server):
        if server.name == "task":
            return [
                StructuredTool.from_function(name="create_task", description="fake", func=_create_task),
                StructuredTool.from_function(name="create_subtask", description="fake", func=_create_subtask),
                StructuredTool.from_function(name="list_tasks", description="fake", func=_list_tasks),
                StructuredTool.from_function(name="update_status", description="fake", func=_update_status),
                StructuredTool.from_function(name="get_task", description="fake", func=_get_task),
            ]
        return []
    return _loader


def test_project_management_module_exports():
    from agentteam.presets import project_management
    assert isinstance(project_management.TEAM, Team)
    assert isinstance(project_management.LIB_AGENTS, list)
    assert isinstance(project_management.METADATA, dict)


def test_project_management_metadata_required_keys():
    from agentteam.presets import project_management
    meta = project_management.METADATA
    for key in ("name", "title", "description", "category", "tags",
                "deps_teams", "deps_library"):
        assert key in meta
    assert meta["name"] == "project_management"
    assert meta["category"] == "productivity"


def test_project_management_has_task_mcp():
    """Team 级挂载 task MCP。"""
    from agentteam.presets import project_management
    team = project_management.TEAM
    mcp_names = {s.name for s in team.mcp_servers}
    assert {"task"} == mcp_names


def test_project_management_has_library_agents():
    """project_management 依赖专家库 risk_assessor。"""
    from agentteam.presets import project_management
    assert len(project_management.LIB_AGENTS) == 1
    assert project_management.LIB_AGENTS[0].name == "risk_assessor"
    assert project_management.METADATA["deps_library"] == ["risk_assessor"]


def test_project_management_has_approval_policies():
    """task_breaker 与 progress_tracker 应有 tool 级审批。"""
    from agentteam.presets import project_management
    team = project_management.TEAM

    def _find_agent(agent, name):
        if agent.name == name:
            return agent
        for child in agent.children:
            if isinstance(child, Agent):
                found = _find_agent(child, name)
                if found:
                    return found
        return None

    task_breaker = _find_agent(team.root, "task_breaker")
    progress_tracker = _find_agent(team.root, "progress_tracker")
    assert task_breaker is not None and task_breaker.approval_policy is not None
    assert "mcp:task:create_task" in task_breaker.approval_policy.targets
    assert progress_tracker is not None and progress_tracker.approval_policy is not None
    assert "mcp:task:update_status" in progress_tracker.approval_policy.targets


def test_project_management_team_compiles():
    from agentteam.presets import project_management
    mod = project_management
    fake_llm = FakeLLM()
    provider = FakeModelProvider({"qwen-max": fake_llm})
    reg = ToolRegistry(mcp_loader=_make_fake_mcp_loader())
    register_builtin_skills(reg)

    # 注册专家库(供 ref="library:risk_assessor" 解析)
    lib = AgentLibrary()
    for a in mod.LIB_AGENTS:
        lib.register(a)

    compiler = TeamCompiler(provider, reg, library=lib)
    graph = compiler.compile(mod.TEAM)
    node_names = set(graph.get_graph().nodes.keys())
    assert "leader_plan" in node_names  # project_manager 是 root supervisor
    assert "worker_task_breaker" in node_names
    assert "worker_progress_tracker" in node_names
    assert "worker_risk_assessor" in node_names
