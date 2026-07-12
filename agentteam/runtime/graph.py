from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agentteam.domain.team import Team
from agentteam.models.provider import ModelProvider
from agentteam.runtime.approval import make_step_gate, make_worker_gate
from agentteam.runtime.nodes import (
    make_leader_plan_node,
    make_leader_review_node,
    make_worker_node,
)
from agentteam.runtime.state import TeamState, is_rejected
from agentteam.runtime.trace import TraceWriter
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


def route_to_worker(state: TeamState) -> str:
    """统一路由：拒绝→END，无更多步骤→END，否则→下一步 worker。"""
    if is_rejected(state):
        return END
    return route_from_review(state)


def make_route_after_worker_gate(worker_node_name: str):
    """创建 worker_gate 之后的路由函数：拒绝→END，否则→worker。"""

    def route(state: TeamState) -> str:
        if is_rejected(state):
            return END
        return worker_node_name

    return route


class TeamCompiler:
    """把 Team 配置编译成可执行的 LangGraph StateGraph。"""

    def __init__(self, model_provider: ModelProvider, tool_registry: ToolRegistry):
        self._mp = model_provider
        self._tr = tool_registry

    def compile(
        self,
        team: Team,
        checkpointer=None,
        trace_writer: TraceWriter | None = None,
        audit_repo=None,
    ):
        graph = StateGraph(TeamState)

        # 加载 MCP 工具到 registry（编译时 eager loading）
        for server in team.mcp_servers:
            self._tr.register_mcp_tools(server)

        leader_model = team.leader.model or team.default_model
        leader_llm = self._mp.get_llm(leader_model)
        graph.add_node(
            "leader_plan", make_leader_plan_node(team.leader, leader_llm, trace_writer)
        )
        graph.add_node(
            "leader_review",
            make_leader_review_node(team.leader, leader_llm, trace_writer),
        )

        # Step gate（仅在 leader 有 step 级策略时添加）
        step_policy = team.leader.approval_policy
        has_step_gate = step_policy is not None and step_policy.level == "step"
        if has_step_gate:
            graph.add_node(
                "step_gate", make_step_gate(step_policy, trace_writer, audit_repo)
            )

        # Worker 节点 + worker gate
        worker_gates: dict[str, bool] = {}
        for worker in team.workers:
            worker_model = worker.model or team.default_model
            llm = self._mp.get_llm(worker_model)
            tools = self._tr.get_tools(worker.tools) if worker.tools else []
            graph.add_node(
                f"worker_{worker.name}",
                make_worker_node(worker, llm, tools, trace_writer, audit_repo),
            )

            wp = worker.approval_policy
            has_gate = wp is not None and wp.level == "worker"
            if has_gate:
                graph.add_node(
                    f"worker_gate_{worker.name}",
                    make_worker_gate(worker.name, wp, trace_writer, audit_repo),
                )
            worker_gates[worker.name] = has_gate

        # 路由目标映射：逻辑名 → 物理节点名（gate 或 worker）
        def physical_target(worker_name: str) -> str:
            if worker_gates.get(worker_name):
                return f"worker_gate_{worker_name}"
            return f"worker_{worker_name}"

        worker_targets = {
            f"worker_{w.name}": physical_target(w.name) for w in team.workers
        }
        worker_targets[END] = END

        # 边
        graph.add_edge(START, "leader_plan")

        if has_step_gate:
            graph.add_edge("leader_plan", "step_gate")
            graph.add_conditional_edges("step_gate", route_to_worker, worker_targets)
        else:
            graph.add_conditional_edges(
                "leader_plan", route_from_plan, worker_targets
            )

        # worker_gate → worker（条件边：拒绝→END）
        for worker in team.workers:
            if worker_gates[worker.name]:
                gate_name = f"worker_gate_{worker.name}"
                worker_node = f"worker_{worker.name}"
                graph.add_conditional_edges(
                    gate_name,
                    make_route_after_worker_gate(worker_node),
                    {worker_node: worker_node, END: END},
                )

        # worker → leader_review
        for worker in team.workers:
            graph.add_edge(f"worker_{worker.name}", "leader_review")

        # leader_review → step_gate 或直接路由
        if has_step_gate:
            graph.add_edge("leader_review", "step_gate")
        else:
            graph.add_conditional_edges(
                "leader_review", route_from_review, worker_targets
            )

        return graph.compile(checkpointer=checkpointer)
