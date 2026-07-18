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


def _eval_condition(cond: str, state: dict) -> bool:
    """在受限 globals 下求值 dag step 的 condition 表达式。

    安全考量:
    - globals 仅暴露安全内置函数(len/sum/min/max/any/all/str/int/float/bool),
      不暴露 __import__/open/exec/eval/compile 等危险函数
    - locals 仅暴露 state 中的可读字段(worker_outputs/completed_steps/skipped_steps/task),
      避免暴露 run_id/pending_approval 等内部字段
    - 任何异常(SyntaxError/NameError/TypeError/ZeroDivisionError 等)返回 False,
      宁可跳过该 step 也不让图崩溃
    - 不允许 import:因为 __builtins__ 被替换,import 语句会抛 ImportError → 返回 False

    用法: condition="len(worker_outputs) >= 2" 或 condition="'step_a' in completed_steps"
    """
    safe_builtins = {
        "len": len, "sum": sum, "min": min, "max": max,
        "any": any, "all": all, "sorted": sorted,
        "str": str, "int": int, "float": float, "bool": bool,
        "True": True, "False": False, "None": None,
    }
    safe_locals = {
        k: v for k, v in state.items()
        if k in ("worker_outputs", "completed_steps", "skipped_steps", "task")
    }
    try:
        result = eval(cond, {"__builtins__": safe_builtins}, safe_locals)
        return bool(result)
    except Exception:
        return False


def _detect_dag_cycle(plan: list[dict]) -> bool:
    """拓扑排序(Kahn 算法)检测 plan 中的循环依赖。

    plan 中的 step 通过 depends_on 形成有向图:dep → step。
    若拓扑排序后访问的节点数 < 总节点数,说明存在环。

    返回 True 表示有环(应拒绝该 plan),False 表示无环。
    """
    # 收集所有节点(step id + 依赖的 id)
    nodes: set[str] = set()
    for step in plan:
        sid = step.get("id") or step.get("worker")
        nodes.add(sid)
        for dep in step.get("depends_on", []):
            nodes.add(dep)

    # 构建邻接表与入度
    adj: dict[str, list[str]] = {n: [] for n in nodes}
    in_degree: dict[str, int] = {n: 0 for n in nodes}
    for step in plan:
        sid = step.get("id") or step.get("worker")
        for dep in step.get("depends_on", []):
            adj[dep].append(sid)
            in_degree[sid] += 1

    # Kahn 算法:BFS 拓扑排序
    queue = [n for n in nodes if in_degree[n] == 0]
    visited = 0
    while queue:
        n = queue.pop(0)
        visited += 1
        for m in adj[n]:
            in_degree[m] -= 1
            if in_degree[m] == 0:
                queue.append(m)

    return visited != len(nodes)


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


def make_route_from_plan_dag(child_targets: dict[str, str]):
    """创建 dag 路由函数:返回 ready steps 的物理节点名列表(LangGraph 并行触发)。

    dag 路由算法(spec §3.2):
    1. 遍历 plan 中所有 step
    2. 跳过已完成(completed_steps)或已跳过(skipped_steps)的 step
    3. 检查 depends_on:所有依赖必须在 completed_steps 或 skipped_steps 中
       (skipped 视为满足,避免被跳过的 step 阻塞后继)
    4. 若 step 有 condition,用 _eval_condition 求值:
       - False: 加入 skipped_steps(in-place mutation),不返回
       - True 或无 condition: 加入 ready 列表
    5. 返回 ready 列表(物理节点名);空则返回 [END]

    返回 list[str] 而非 str:LangGraph conditional_edges 收到 list 时并行触发所有目标。
    """
    def route(state):
        # dag 模式下若被拒绝(工具审批 reject),直接结束
        if is_rejected(state):
            return [END]
        plan = state.get("plan", [])
        completed = state.get("completed_steps", set())
        skipped = state.get("skipped_steps", set())
        ready: list[str] = []
        for step in plan:
            sid = step.get("id") or step.get("worker")
            if sid in completed or sid in skipped:
                continue
            deps = step.get("depends_on", [])
            if all(d in completed or d in skipped for d in deps):
                cond = step.get("condition")
                if cond and not _eval_condition(cond, state):
                    # condition False:标记跳过,不返回(in-place mutation)
                    skipped.add(sid)
                    continue
                ready.append(child_targets[step["worker"]])
        return ready if ready else [END]
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
        # visited_team_names 跟踪已被引用的 Team.name（唯一标识），
        # 用于检测循环引用。root Team 自身先入集合，确保自引用 A→A 被识别。
        return self._compile_agent(
            team.root, team.default_model, checkpointer,
            trace_writer, audit_repo,
            depth=0, path=f"team:{team.name}",
            visited_team_names={team.name},
        )

    def _compile_agent(
        self, agent: Agent, default_model, checkpointer,
        trace_writer, audit_repo, depth, path,
        visited_team_names: set[str] | None = None,
    ):
        # 1. 解析 ref（深拷贝库定义，保留覆盖）
        agent = self._lib.resolve(agent)
        # 2. 校验
        self._validate(agent, depth, path)
        # 注册 Agent 级 MCP
        for server in agent.mcp_servers:
            self._tr.register_mcp_tools(server)
        # 3. 按 role 分派
        if agent.role == "worker":
            return self._compile_worker(agent, default_model, trace_writer, audit_repo)
        return self._compile_supervisor(
            agent, default_model, checkpointer, trace_writer, audit_repo,
            depth, path, visited_team_names or set(),
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
        trace_writer, audit_repo, depth, path, visited_team_names,
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
                # BUG-04 修复：用被引用 Team 的唯一 name（而非 alias）做循环检测。
                # alias 可被不同 sub-team 引用链复用（如 A→B(alias=x)→C(alias=x)），
                # 用 alias 会假阳性。Team.name 是 Team 注册时的唯一标识，真正反映引用关系。
                if sub_team.name in visited_team_names:
                    raise ValueError(
                        f"Circular team reference: {sub_team.name} "
                        f"(path: {path}.{alias})"
                    )
                # 注册 TeamRef 的 mcp_overrides
                for server in child.mcp_overrides:
                    self._tr.register_mcp_tools(server)
                # 递归编译时把 sub_team.name 加入已访问集合，子层级的非 TeamRef
                # children 会原样透传该集合（不增不减），保证跨层级循环检测正确。
                sub_graph = self._compile_agent(
                    sub_team.root, sub_team.default_model, checkpointer,
                    trace_writer, audit_repo,
                    depth=depth + 1, path=f"{path}.{alias}",
                    visited_team_names=visited_team_names | {sub_team.name},
                )
                node_name = f"subteam_{alias}"
                graph.add_node(node_name, make_supervisor_node(sub_graph, alias))
                child_targets[alias] = node_name
                worker_gates[alias] = False
            else:
                sub_graph = self._compile_agent(
                    child, default_model, checkpointer, trace_writer, audit_repo,
                    depth=depth + 1, path=f"{path}.{child.name}",
                    visited_team_names=visited_team_names,
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
