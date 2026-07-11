from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agentteam.domain.team import Team
from agentteam.models.provider import ModelProvider
from agentteam.runtime.nodes import (
    make_leader_plan_node,
    make_leader_review_node,
    make_worker_node,
)
from agentteam.runtime.state import TeamState
from agentteam.tools.registry import ToolRegistry


def route_from_plan(state: TeamState) -> str:
    """leader_plan 之后，路由到第一步的 worker；空计划直接结束。"""
    plan = state.get("plan", [])
    if not plan:
        return END
    return f"worker_{plan[0]['worker']}"


def route_from_review(state: TeamState) -> str:
    """leader_review 之后，若还有步骤路由到下一个 worker，否则结束。"""
    current = state.get("current_step", 0)
    plan = state.get("plan", [])
    if current >= len(plan):
        return END
    return f"worker_{plan[current]['worker']}"


class TeamCompiler:
    """把 Team 配置编译成可执行的 LangGraph StateGraph。"""

    def __init__(self, model_provider: ModelProvider, tool_registry: ToolRegistry):
        self._mp = model_provider
        self._tr = tool_registry

    def compile(self, team: Team, checkpointer=None):
        graph = StateGraph(TeamState)

        leader_model = team.leader.model or team.default_model
        leader_llm = self._mp.get_llm(leader_model)
        graph.add_node("leader_plan", make_leader_plan_node(team.leader, leader_llm))
        graph.add_node("leader_review", make_leader_review_node(team.leader, leader_llm))

        for worker in team.workers:
            worker_model = worker.model or team.default_model
            llm = self._mp.get_llm(worker_model)
            tools = self._tr.get_tools(worker.tools) if worker.tools else []
            graph.add_node(
                f"worker_{worker.name}", make_worker_node(worker, llm, tools)
            )

        graph.add_edge(START, "leader_plan")

        worker_targets = {f"worker_{w.name}": f"worker_{w.name}" for w in team.workers}
        worker_targets[END] = END
        graph.add_conditional_edges("leader_plan", route_from_plan, worker_targets)

        for worker in team.workers:
            graph.add_edge(f"worker_{worker.name}", "leader_review")

        graph.add_conditional_edges("leader_review", route_from_review, worker_targets)

        return graph.compile(checkpointer=checkpointer)
