"""customer_support 预置团队测试。"""
from langchain_core.tools import StructuredTool

from agentteam.domain.agent import Agent
from agentteam.domain.team import Team
from agentteam.runtime.graph import TeamCompiler
from agentteam.tools.registry import ToolRegistry
from agentteam.tools.skills import register_builtin_skills
from tests.conftest import FakeLLM, FakeModelProvider


def _make_fake_mcp_loader():
    """fake mcp_loader:产出 ticket 工具。"""
    def _list_tickets() -> str:
        return "[]"

    def _get_ticket() -> str:
        return "{}"

    def _create_note() -> str:
        return "ok"

    def _resolve_ticket() -> str:
        return "resolved"

    def _escalate_complaint() -> str:
        return "escalated"

    fake_tools = [
        StructuredTool.from_function(name=n, description=f"fake {n}", func=f)
        for n, f in [
            ("list_tickets", _list_tickets),
            ("get_ticket", _get_ticket),
            ("create_note", _create_note),
            ("resolve_ticket", _resolve_ticket),
            ("escalate_complaint", _escalate_complaint),
        ]
    ]
    return lambda server: fake_tools


def test_customer_support_module_exports():
    from agentteam.presets import customer_support
    assert isinstance(customer_support.TEAM, Team)
    assert isinstance(customer_support.LIB_AGENTS, list)
    assert isinstance(customer_support.METADATA, dict)


def test_customer_support_metadata_required_keys():
    from agentteam.presets import customer_support
    meta = customer_support.METADATA
    for key in ("name", "title", "description", "category", "tags",
                "deps_teams", "deps_library"):
        assert key in meta
    assert meta["name"] == "customer_support"
    assert meta["category"] == "support"


def test_customer_support_lib_agents_empty():
    """客服团队无专家库依赖。"""
    from agentteam.presets import customer_support
    assert customer_support.LIB_AGENTS == []
    assert customer_support.METADATA["deps_library"] == []


def test_customer_support_has_mcp():
    from agentteam.presets import customer_support
    team = customer_support.TEAM
    assert len(team.mcp_servers) > 0, "应挂载 ticket MCP"


def test_customer_support_has_approval_policy():
    """escalation(supervisor)与 complaint_handler(worker)应有审批策略。"""
    from agentteam.presets import customer_support
    team = customer_support.TEAM

    def _find_agent(agent, name):
        if agent.name == name:
            return agent
        for child in agent.children:
            if isinstance(child, Agent):
                found = _find_agent(child, name)
                if found:
                    return found
        return None

    escalation = _find_agent(team.root, "escalation")
    complaint_handler = _find_agent(team.root, "complaint_handler")
    assert escalation is not None and escalation.approval_policy is not None
    assert complaint_handler is not None and complaint_handler.approval_policy is not None


def test_customer_support_team_compiles():
    from agentteam.presets import customer_support
    mod = customer_support
    fake_llm = FakeLLM()
    provider = FakeModelProvider({"qwen-max": fake_llm})
    reg = ToolRegistry(mcp_loader=_make_fake_mcp_loader())
    register_builtin_skills(reg)
    compiler = TeamCompiler(provider, reg)
    graph = compiler.compile(mod.TEAM)
    node_names = set(graph.get_graph().nodes.keys())
    assert "leader_plan" in node_names  # support_manager 是 root supervisor
    assert "worker_frontline" in node_names
    assert "worker_complaint_handler" in node_names
    # escalation 是 supervisor,有自己的 leader_plan(节点名含 escalation)
    assert any("escalation" in n for n in node_names)
