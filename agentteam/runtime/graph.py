"""TeamCompiler：递归编译 Agent 树为 LangGraph StateGraph。"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agentteam.domain.agent import Agent, TeamRef
from agentteam.domain.library import AgentLibrary
from agentteam.domain.team import Team
from agentteam.models.provider import ModelProvider
from agentteam.runtime.approval import make_step_gate, make_worker_gate
from agentteam.runtime.nodes import (
    make_leader_plan_node,
    make_leader_review_node,
    make_supervisor_node,
    make_worker_node,
)
from agentteam.runtime.state import TeamState, is_rejected
from agentteam.runtime.trace import TraceWriter
from agentteam.tools.registry import ToolRegistry


def route_from_plan(state: TeamState) -> str:
    """旧模块级函数：仅用于旧测试兼容。

    新代码用 make_route_from_plan(child_targets) 工厂。
    """
    plan = state.get("plan", [])
    if not plan:
        return END
    return f"worker_{plan[0]['worker']}"


def route_from_review(state: TeamState) -> str:
    """旧模块级函数：仅用于旧测试兼容。"""
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


def make_route_from_plan(child_targets: dict[str, str]):
    """创建路由函数：plan[0].worker → child_targets[name]。"""
    def route(state: TeamState) -> str:
        plan = state.get("plan", [])
        if not plan:
            return END
        return child_targets[plan[0]["worker"]]
    return route


def make_route_from_review(child_targets: dict[str, str]):
    """创建路由函数：current_step → child_targets[name]。"""
    def route(state: TeamState) -> str:
        current = state.get("current_step", 0)
        plan = state.get("plan", [])
        if current >= len(plan):
            return END
        return child_targets[plan[current]["worker"]]
    return route


def make_route_to_worker(child_targets: dict[str, str]):
    """创建路由函数：拒绝→END，否则→make_route_from_review。"""
    inner = make_route_from_review(child_targets)
    def route(state: TeamState) -> str:
        if is_rejected(state):
            return END
        return inner(state)
    return route


def make_route_after_worker_gate(worker_node_name: str):
    """创建 worker_gate 之后的路由函数：拒绝→END，否则→worker。"""
    def route(state: TeamState) -> str:
        if is_rejected(state):
            return END
        return worker_node_name
    return route


class TeamCompiler:
    """把 Team 配置（Agent 树）递归编译成可执行的 LangGraph StateGraph。"""

    MAX_DEPTH = 8

    def __init__(
        self,
        model_provider: ModelProvider,
        tool_registry: ToolRegistry,
        library: AgentLibrary | None = None,
    ):
        self._mp = model_provider
        self._tr = tool_registry
        self._lib = library or AgentLibrary()
        self._team_registry: dict[str, Team] = {}

    def register_team(self, team: Team) -> None:
        """注册可被 TeamRef 引用的 Team。"""
        self._team_registry[team.name] = team

    def register_library(self, library: AgentLibrary) -> None:
        """注入专家库（也可在构造时传入）。"""
        self._lib = library

    def compile(
        self,
        team: Team,
        checkpointer=None,
        trace_writer: TraceWriter | None = None,
        audit_repo=None,
    ):
        # 加载 team 级 MCP（沿用现状）
        for server in team.mcp_servers:
            self._tr.register_mcp_tools(server)
        # 校验 root
        if team.root.role != "supervisor":
            raise ValueError("Team.root must be supervisor")
        # 递归编译 root
        return self._compile_agent(
            team.root, team.default_model, checkpointer,
            trace_writer, audit_repo,
            depth=0, path=f"team:{team.name}",
        )

    def _compile_agent(
        self, agent: Agent, default_model, checkpointer,
        trace_writer, audit_repo, depth, path,
    ):
        # 1. 解析 ref（深拷贝库定义，保留覆盖）
        agent = self._lib.resolve(agent)
        # 2. 校验
        self._validate(agent, depth, path)
        # 3. 按 role 分派
        if agent.role == "worker":
            return self._compile_worker(agent, default_model, trace_writer, audit_repo)
        return self._compile_supervisor(
            agent, default_model, checkpointer, trace_writer, audit_repo,
            depth, path,
        )

    def _validate(self, agent: Agent, depth: int, path: str) -> None:
        if depth > self.MAX_DEPTH:
            raise ValueError(f"Max depth exceeded: >{self.MAX_DEPTH} at {path}")
        if agent.role == "supervisor":
            if not agent.children:
                raise ValueError(f"supervisor must have children: {agent.name}")
            if agent.tools:
                raise ValueError(f"supervisor cannot have tools: {agent.name}")
        elif agent.role == "worker":
            if agent.children:
                raise ValueError(f"worker cannot have children: {agent.name}")
        else:
            raise ValueError(f"Unknown role: {agent.role}")

    def _compile_supervisor(
        self, agent: Agent, default_model, checkpointer,
        trace_writer, audit_repo, depth, path,
    ):
        graph = StateGraph(TeamState)
        llm = self._mp.get_llm(agent.model or default_model)

        # leader_plan
        graph.add_node("leader_plan",
            make_leader_plan_node(agent, llm, trace_writer))

        # step_gate（仅当本层 step 级审批）
        step_policy = agent.approval_policy
        has_step_gate = step_policy is not None and step_policy.level == "step"
        if has_step_gate:
            graph.add_node("step_gate",
                make_step_gate(step_policy, trace_writer, audit_repo))

        # 递归编译 children
        child_targets: dict[str, str] = {}  # logical name → physical node name
        worker_gates: dict[str, bool] = {}

        for child in agent.children:
            if isinstance(child, TeamRef):
                sub_team = self._team_registry.get(child.name)
                if sub_team is None:
                    raise KeyError(f"Team not registered: {child.name}")
                alias = child.alias or sub_team.root.name
                if alias in path.split("."):
                    raise ValueError(
                        f"Circular team reference: {path}.{alias}"
                    )
                sub_graph = self._compile_agent(
                    sub_team.root, sub_team.default_model, checkpointer,
                    trace_writer, audit_repo,
                    depth=depth + 1, path=f"{path}.{alias}",
                )
                node_name = f"subteam_{alias}"
                graph.add_node(node_name, make_supervisor_node(sub_graph, alias))
                child_targets[alias] = node_name
                worker_gates[alias] = False
            else:
                sub_graph = self._compile_agent(
                    child, default_model, checkpointer, trace_writer, audit_repo,
                    depth=depth + 1, path=f"{path}.{child.name}",
                )
                # worker 用 worker_{name} 保持与旧测试兼容；supervisor 用 agent_{name}
                if child.role == "worker":
                    node_name = f"worker_{child.name}"
                    graph.add_node(node_name, sub_graph)  # worker 已由 make_worker_node 包装
                else:
                    node_name = f"agent_{child.name}"
                    graph.add_node(node_name, make_supervisor_node(sub_graph, child.name))
                child_targets[child.name] = node_name

                # worker 级审批 gate（仅 worker 角色可能有）
                wp = child.approval_policy
                has_gate = wp is not None and wp.level == "worker"
                if has_gate:
                    gate_name = f"worker_gate_{child.name}"
                    graph.add_node(
                        gate_name,
                        make_worker_gate(child.name, wp, trace_writer, audit_repo),
                    )
                worker_gates[child.name] = has_gate

        # leader_review
        graph.add_node("leader_review",
            make_leader_review_node(agent, llm, trace_writer))

        # 路由目标映射：key 必须是路由函数返回值（物理节点名），
        # value 是实际目标节点（gate 或节点本身）。与旧实现 worker_targets
        # 结构一致：key 与无 gate 时的 value 相同。
        physical_targets: dict[str, str] = {}
        for logical, node_name in child_targets.items():
            if worker_gates.get(logical):
                physical_targets[node_name] = f"worker_gate_{logical}"
            else:
                physical_targets[node_name] = node_name
        physical_targets[END] = END

        # 边：START → leader_plan
        graph.add_edge(START, "leader_plan")

        # leader_plan → step_gate 或直接路由
        # 路由函数接收 child_targets（logical→physical），返回物理节点名；
        # path_map 用 physical_targets（physical→destination，含 gate 重定向）。
        if has_step_gate:
            graph.add_edge("leader_plan", "step_gate")
            graph.add_conditional_edges(
                "step_gate",
                make_route_to_worker(child_targets),
                physical_targets,
            )
        else:
            graph.add_conditional_edges(
                "leader_plan",
                make_route_from_plan(child_targets),
                physical_targets,
            )

        # worker_gate → agent（条件边：拒绝→END）
        for logical, has_gate in worker_gates.items():
            if has_gate:
                gate_name = f"worker_gate_{logical}"
                target_node = child_targets[logical]
                graph.add_conditional_edges(
                    gate_name,
                    make_route_after_worker_gate(target_node),
                    {target_node: target_node, END: END},
                )

        # agent/subteam/worker → leader_review
        for logical, node_name in child_targets.items():
            graph.add_edge(node_name, "leader_review")

        # leader_review → step_gate 或直接路由
        if has_step_gate:
            graph.add_edge("leader_review", "step_gate")
        else:
            graph.add_conditional_edges(
                "leader_review",
                make_route_from_review(child_targets),
                physical_targets,
            )

        return graph.compile(checkpointer=checkpointer)

    def _compile_worker(
        self, agent: Agent, default_model, trace_writer, audit_repo,
    ):
        """worker 沿用 make_worker_node（内部封装子图并剥离共享累加器字段，
        避免子图 reducer 与父图 reducer 双重累积）。
        """
        llm = self._mp.get_llm(agent.model or default_model)
        tools = self._tr.get_tools(agent.tools) if agent.tools else []
        return make_worker_node(agent, llm, tools, trace_writer, audit_repo)
