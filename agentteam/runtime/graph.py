"""TeamCompiler：递归编译 Agent 树为 LangGraph StateGraph。"""
from __future__ import annotations

import ast
import operator as _op
import threading
from collections import deque
from typing import Any

from langgraph.graph import END, START, StateGraph

from agentteam.domain.agent import Agent, TeamRef
from agentteam.domain.library import AgentLibrary
from agentteam.domain.team import Team
from agentteam.logging_config import get_logger
from agentteam.models.provider import ModelProvider
from agentteam.runtime.approval import make_step_gate, make_worker_gate
from agentteam.runtime.nodes import (
    make_leader_plan_node,
    make_leader_review_node,
    make_supervisor_node,
    make_worker_node,
)
from agentteam.runtime.skills import SkillLoader
from agentteam.runtime.state import TeamState, is_rejected
from agentteam.runtime.trace import TraceWriter
from agentteam.tools.registry import ToolRegistry

logger = get_logger("runtime.graph")


# ---- 安全表达式求值(替代 eval) ----
# 仅支持白名单 AST 节点 + 白名单运算符 + 白名单内置函数,
# 阻断 __class__/__bases__/__subclasses__() 等对象模型逃逸路径。
# condition 字段承载 LLM 生成内容,必须视为不可信输入。
_ALLOWED_AST_NODES = (
    ast.Expression, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare,
    ast.Constant, ast.Name, ast.Load, ast.List, ast.Tuple, ast.Dict, ast.Set,
    ast.IfExp, ast.Call,
)
_ALLOWED_BINOPS = {
    ast.Add: _op.add, ast.Sub: _op.sub, ast.Mult: _op.mul,
    ast.Div: _op.truediv, ast.FloorDiv: _op.floordiv, ast.Mod: _op.mod,
    ast.Pow: _op.pow,
}
_ALLOWED_UNARYOPS = {
    ast.UAdd: _op.pos, ast.USub: _op.neg, ast.Not: _op.not_,
}
_ALLOWED_CMPOPS = {
    ast.Eq: _op.eq, ast.NotEq: _op.ne, ast.Lt: _op.lt, ast.LtE: _op.le,
    ast.Gt: _op.gt, ast.GtE: _op.ge, ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}
_ALLOWED_NAMES = {
    "True": True, "False": False, "None": None,
}
_ALLOWED_FUNCS = {
    "len": len, "sum": sum, "min": min, "max": max,
    "any": any, "all": all, "sorted": sorted,
    "str": str, "int": int, "float": float, "bool": bool,
}


class _SafeEvalError(Exception):
    """安全求值失败(语法错误/不支持节点/未知名称等)。"""


def _safe_eval_node(node: ast.AST, env: dict) -> object:
    """递归求值 AST 节点,严格白名单。"""
    if not isinstance(node, _ALLOWED_AST_NODES):
        raise _SafeEvalError(f"Unsupported AST node: {type(node).__name__}")
    if isinstance(node, ast.Expression):
        return _safe_eval_node(node.body, env)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        if node.id in _ALLOWED_NAMES:
            return _ALLOWED_NAMES[node.id]
        raise _SafeEvalError(f"Unknown name: {node.id}")
    if isinstance(node, ast.BoolOp):
        values = [_safe_eval_node(v, env) for v in node.values]
        if isinstance(node.op, ast.And):
            result = True
            for v in values:
                if not v:
                    return v
                result = v
            return result
        else:  # Or
            for v in values:
                if v:
                    return v
            return values[-1] if values else False
    if isinstance(node, ast.BinOp):
        left = _safe_eval_node(node.left, env)
        right = _safe_eval_node(node.right, env)
        func = _ALLOWED_BINOPS.get(type(node.op))
        if func is None:
            raise _SafeEvalError(f"Unsupported binop: {type(node.op).__name__}")
        return func(left, right)
    if isinstance(node, ast.UnaryOp):
        operand = _safe_eval_node(node.operand, env)
        func = _ALLOWED_UNARYOPS.get(type(node.op))
        if func is None:
            raise _SafeEvalError(f"Unsupported unaryop: {type(node.op).__name__}")
        return func(operand)
    if isinstance(node, ast.Compare):
        left = _safe_eval_node(node.left, env)
        for op, comparator in zip(node.ops, node.comparators):
            right = _safe_eval_node(comparator, env)
            func = _ALLOWED_CMPOPS.get(type(op))
            if func is None:
                raise _SafeEvalError(f"Unsupported cmpop: {type(op).__name__}")
            if not func(left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        return (
            _safe_eval_node(node.body, env)
            if _safe_eval_node(node.test, env)
            else _safe_eval_node(node.orelse, env)
        )
    if isinstance(node, ast.List):
        return [_safe_eval_node(e, env) for e in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_safe_eval_node(e, env) for e in node.elts)
    if isinstance(node, ast.Set):
        return {_safe_eval_node(e, env) for e in node.elts}
    if isinstance(node, ast.Dict):
        return {
            _safe_eval_node(k, env): _safe_eval_node(v, env)
            for k, v in zip(node.keys, node.values)
        }
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise _SafeEvalError("Only direct function calls allowed")
        func_name = node.func.id
        func = _ALLOWED_FUNCS.get(func_name)
        if func is None:
            raise _SafeEvalError(f"Unknown function: {func_name}")
        args = [_safe_eval_node(a, env) for a in node.args]
        if node.keywords:
            raise _SafeEvalError("Keyword arguments not allowed")
        return func(*args)
    raise _SafeEvalError(f"Unhandled node: {type(node).__name__}")


def _eval_condition(cond: str, state: dict) -> bool:
    """安全求值 dag step 的 condition 表达式(替代 eval)。

    实现策略:用 ast.parse 解析为 AST,然后只接受白名单节点类型/运算符/
    内置函数/字段名。阻断 __class__/__bases__/__subclasses__() 等逃逸路径,
    适用于承载 LLM 生成内容的 condition 字段。

    支持语法示例:
      - "len(worker_outputs) >= 2"
      - "'step_a' in completed_steps"
      - "any(s in completed_steps for s in ['a', 'b'])"  # 注意:不支持 generator,改用 any(['a','b'])
      - "len(completed_steps) > 0 and 'step_b' not in skipped_steps"

    任何异常(语法错误/不支持/求值错误)返回 False——宁可跳过该 step 也不让图崩溃。
    """
    safe_locals = {
        k: v for k, v in state.items()
        if k in ("worker_outputs", "completed_steps", "skipped_steps", "task")
    }
    try:
        tree = ast.parse(cond, mode="eval")
        result = _safe_eval_node(tree, safe_locals)
        return bool(result)
    except _SafeEvalError:
        return False
    except Exception:
        # P2-6:原裸 except 静默吞所有异常,condition 是 LLM 生成内容,
        # 调试时完全看不到为何 step 被跳过。加日志便于排障。
        logger.warning("condition eval failed: %r", cond, exc_info=True)
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

    # Kahn 算法:BFS 拓扑排序(用 deque 保证 popleft() 为 O(1),整体 O(V+E))
    queue = deque(n for n in nodes if in_degree[n] == 0)
    visited = 0
    while queue:
        n = queue.popleft()
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
    def route(state: TeamState) -> list[str]:
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


def make_route_unified(child_targets: dict[str, str]):
    """统一路由:运行时根据 state["execution_mode"] 分派到 sequential 或 dag 逻辑。

    LangGraph add_conditional_edges 只能注册一个路由函数,因此用 unified 函数
    在运行时根据 execution_mode 分派(编译期无法预判 LLM 输出的 execution_mode)。

    - sequential: 用 make_route_from_review 逻辑(current_step -> worker)
    - dag: 用 make_route_from_plan_dag 逻辑(completed_steps -> ready workers)
    """
    dag_router = make_route_from_plan_dag(child_targets)
    seq_router = make_route_from_review(child_targets)

    def route(state):
        if state.get("execution_mode") == "dag":
            return dag_router(state)
        return seq_router(state)
    return route


def make_route_unified_to_worker(child_targets: dict[str, str]):
    """统一路由(带拒绝检查): 拒绝->END, 否则按 execution_mode 分派。

    用于 step_gate 之后:sequential 用 make_route_from_review(拒绝已在分派前检查),
    dag 用 dag_router。
    """
    dag_router = make_route_from_plan_dag(child_targets)
    seq_router = make_route_from_review(child_targets)

    def route(state):
        if is_rejected(state):
            return END
        if state.get("execution_mode") == "dag":
            return dag_router(state)
        return seq_router(state)
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


class RoleSpec:
    """角色编译规范(P3-2 节点工厂注册表)。

    把 _compile_agent / _validate 中分散的 role 硬编码 if/else 收敛为
    数据驱动注册表,允许第三方扩展新 role(如 "reviewer"/"validator")
    而无需修改 TeamCompiler 源码。

    字段:
        compile_fn: 编译函数,签名
            (compiler, agent, default_model, checkpointer, trace_writer,
             audit_repo, depth, path, visited_team_names) -> compiled
            其中 compiler 是 TeamCompiler 实例(供 compile_fn 复用其 _mp/_tr 等)。
        validate_fn: 校验函数,签名 (agent, depth, path) -> None(异常即校验失败)。
        is_subgraph: 该 role 编译产物是否为"子图"(worker=True,直接 add_node;
            supervisor=False,需用 make_supervisor_node 包装后再 add_node)。
            影响 _compile_supervisor 内 child 节点命名前缀(worker_ vs agent_)。
    """

    def __init__(self, compile_fn, validate_fn, is_subgraph: bool) -> None:
        self.compile_fn = compile_fn
        self.validate_fn = validate_fn
        self.is_subgraph = is_subgraph


class RoleRegistry:
    """role → RoleSpec 注册表(class-level 单例)。

    使用:
        # 注册新 role
        RoleRegistry.register("reviewer", RoleSpec(
            compile_fn=my_reviewer_compile,
            validate_fn=my_reviewer_validate,
            is_subgraph=True,
        ))
        # 查询
        spec = RoleRegistry.get("reviewer")
    """

    _specs: dict[str, RoleSpec] = {}

    @classmethod
    def register(cls, role: str, spec: RoleSpec) -> None:
        """注册 role。重复注册覆盖(便于测试 monkeypatch 恢复)。"""
        cls._specs[role] = spec

    @classmethod
    def unregister(cls, role: str) -> bool:
        return cls._specs.pop(role, None) is not None

    @classmethod
    def get(cls, role: str) -> RoleSpec | None:
        return cls._specs.get(role)

    @classmethod
    def roles(cls) -> list[str]:
        return list(cls._specs.keys())


# ---- 默认 role 注册:worker / supervisor(原硬编码 if/else 逻辑) ----

def _validate_worker(agent: Agent, depth: int, path: str) -> None:
    """worker 角色校验(MAX_DEPTH 检查由 TeamCompiler._validate 统一处理)。"""
    if agent.children:
        raise ValueError(f"worker cannot have children: {agent.name}")


def _validate_supervisor(agent: Agent, depth: int, path: str) -> None:
    """supervisor 角色校验(MAX_DEPTH 检查由 TeamCompiler._validate 统一处理)。"""
    if not agent.children:
        raise ValueError(f"supervisor must have children: {agent.name}")
    if agent.tools:
        raise ValueError(f"supervisor cannot have tools: {agent.name}")


def _compile_worker_via_spec(compiler, agent, default_model, checkpointer,
                             trace_writer, audit_repo, depth, path,
                             visited_team_names):
    """worker 编译入口(适配 RoleSpec.compile_fn 签名)。

    忽略 checkpointer/depth/path/visited_team_names(worker 是叶子节点,
    不递归,不需要这些参数)。
    """
    return compiler._compile_worker(agent, default_model, trace_writer, audit_repo)


def _compile_supervisor_via_spec(compiler, agent, default_model, checkpointer,
                                  trace_writer, audit_repo, depth, path,
                                  visited_team_names):
    """supervisor 编译入口(适配 RoleSpec.compile_fn 签名)。"""
    return compiler._compile_supervisor(
        agent, default_model, checkpointer, trace_writer, audit_repo,
        depth, path, visited_team_names,
    )


# 注册默认 role(模块加载时执行一次)
RoleRegistry.register("worker", RoleSpec(
    compile_fn=_compile_worker_via_spec,
    validate_fn=_validate_worker,
    is_subgraph=True,
))
RoleRegistry.register("supervisor", RoleSpec(
    compile_fn=_compile_supervisor_via_spec,
    validate_fn=_validate_supervisor,
    is_subgraph=False,
))


class TeamRegistry:
    """Team 注册表(P1-3 拆分:从 TeamCompiler 抽出)。

    维护 name → Team 映射,支持 register/get/list/remove。
    独立成类便于:
    - 单独测试注册逻辑(无需 mock ModelProvider/ToolRegistry)
    - 未来支持持久化或分布式注册(如 etcd-backed)而不影响编译器
    - 注册逻辑变化(如校验/版本管理)时只改这一个类
    """

    def __init__(self) -> None:
        self._teams: dict[str, Team] = {}

    def register(self, team: Team) -> None:
        """注册/覆盖同名 team。"""
        self._teams[team.name] = team

    def get(self, name: str) -> Team | None:
        return self._teams.get(name)

    def list_names(self) -> list[str]:
        return list(self._teams.keys())

    def remove(self, name: str) -> bool:
        return self._teams.pop(name, None) is not None

    def clear(self) -> None:
        self._teams.clear()


class CompileCache:
    """编译图缓存(P1-3 拆分:从 TeamCompiler 抽出)。

    缓存 key = (team_name, version_signature, checkpointer_id)。
    相同 key 的 compile 返回同一编译图,避免每次 create_run 重编译。

    缓存失效策略:
    - invalidate(team_name):该 team 的所有版本缓存失效(register_team 重注册时调用)
    - clear():清空全部(register_library 注入新库时调用,因 library.resolve 影响树解析)
    - get/put:线程安全(_lock 保护 dict 读写)

    独立成类便于:
    - 单独测试缓存命中/失效逻辑
    - 未来换 LRU/TTL 策略(当前无界增长,P2-5 风险)只改这一个类
    """

    def __init__(self) -> None:
        self._cache: dict[tuple, Any] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple) -> Any | None:
        with self._lock:
            return self._cache.get(key)

    def put(self, key: tuple, value: Any) -> None:
        with self._lock:
            self._cache[key] = value

    def invalidate(self, team_name: str) -> None:
        """失效指定 team 的所有缓存条目(按 key[0] == team_name 匹配)。"""
        with self._lock:
            keys_to_remove = [k for k in self._cache if k[0] == team_name]
            for k in keys_to_remove:
                self._cache.pop(k, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


class TeamCompiler:
    """把 Team 配置（Agent 树）递归编译成可执行的 LangGraph StateGraph。

    职责拆分(P1-3:从上帝对象拆出 TeamRegistry + CompileCache):
    - TeamRegistry:管理 team 注册表(name → Team 映射)
    - CompileCache:管理编译图缓存(key → compiled graph)
    - TeamCompiler:只负责递归编译逻辑(_compile_agent/_compile_supervisor/_compile_worker)

    外部 API 保持不变(register_team/invalidate/clear_cache 等仍由 TeamCompiler
    暴露,内部委托给对应组件),保证向后兼容。

    编译缓存:相同 (team identity, team version, checkpointer id) 的 compile
    调用返回同一编译图实例,避免每次 create_run 都重新走完整 Agent 树。
    缓存键不含 trace_writer/audit_repo,因为这两者只被节点闭包引用,
    不同 run 的 trace_writer 各异但图结构本身不变。

    缓存失效:
    - register_team 重新注册同名 team → 自动失效该 team 的缓存条目
    - register_library 注入新库 → 整个缓存清空(library.resolve 影响 agent 树)
    - invalidate(team_name) 显式失效单条
    - clear_cache() 清空全部
    """

    MAX_DEPTH = 8

    def __init__(
        self,
        model_provider: ModelProvider,
        tool_registry: ToolRegistry,
        library: AgentLibrary | None = None,
        run_manager=None,
        skill_loader: SkillLoader | None = None,
    ):
        self._mp = model_provider
        self._tr = tool_registry
        self._lib = library or AgentLibrary()
        self._run_manager = run_manager
        self._skill_loader = skill_loader or SkillLoader()
        # P1-3 拆分:Team 注册表与编译缓存独立成类
        self._team_registry = TeamRegistry()
        self._compile_cache = CompileCache()

    def register_team(self, team: Team) -> None:
        """注册可被 TeamRef 引用的 Team。

        重名注册自动失效该 team 的缓存条目(配置已变,旧编译图无效)。
        """
        self._team_registry.register(team)
        self.invalidate(team.name)

    def register_library(self, library: AgentLibrary) -> None:
        """注入专家库（也可在构造时传入）。库可能影响 agent 树解析,清空整个缓存。"""
        self._lib = library
        self.clear_cache()

    def invalidate(self, team_name: str) -> None:
        """失效指定 team 的缓存条目。"""
        self._compile_cache.invalidate(team_name)

    def clear_cache(self) -> None:
        """清空所有编译缓存。"""
        self._compile_cache.clear()

    @staticmethod
    def _version_signature(agent) -> tuple:
        """递归生成 agent 树的版本签名。

        签名包含每个节点的 (name, role, version) + children 递归签名,
        用于检测任一节点 version 变化(进化触发)或结构变化。
        TeamRef 用 (alias, name) 标识,不含 version(子 team 自身的签名
        通过其 root 体现,跨 team 引用本身不变)。
        """
        from agentteam.domain.agent import TeamRef
        children_sig = tuple(
            (c.alias, c.name) if isinstance(c, TeamRef) else TeamCompiler._version_signature(c)
            for c in agent.children
        )
        return (agent.name, agent.role, agent.version, children_sig)

    def compile(
        self,
        team: Team,
        checkpointer=None,
        trace_writer: TraceWriter | None = None,
        audit_repo=None,
    ):
        # 计算缓存键
        checkpointer_id = id(checkpointer) if checkpointer is not None else None
        sig = self._version_signature(team.root)
        cache_key = (team.name, sig, checkpointer_id)
        cached = self._compile_cache.get(cache_key)
        if cached is not None:
            return cached

        # 加载 team 级 MCP（沿用现状）
        for server in team.mcp_servers:
            self._tr.register_mcp_tools(server)
        # 校验 root
        if team.root.role != "supervisor":
            raise ValueError("Team.root must be supervisor")
        # 递归编译 root
        # visited_team_names 跟踪已被引用的 Team.name（唯一标识），
        # 用于检测循环引用。root Team 自身先入集合，确保自引用 A→A 被识别。
        compiled = self._compile_agent(
            team.root, team.default_model, checkpointer,
            trace_writer, audit_repo,
            depth=0, path=f"team:{team.name}",
            visited_team_names={team.name},
        )
        self._compile_cache.put(cache_key, compiled)
        return compiled

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
        # 3. 按 role 分派(数据驱动注册表,替代硬编码 if/else)
        spec = RoleRegistry.get(agent.role)
        if spec is None:
            raise ValueError(f"Unknown role: {agent.role}")
        return spec.compile_fn(
            self, agent, default_model, checkpointer, trace_writer, audit_repo,
            depth, path, visited_team_names or set(),
        )

    def _validate(self, agent: Agent, depth: int, path: str) -> None:
        # MAX_DEPTH 是 TeamCompiler 级不变量,所有 role 共享(测试可调小)
        if depth > self.MAX_DEPTH:
            raise ValueError(f"Max depth exceeded: >{self.MAX_DEPTH} at {path}")
        # role 校验委托给 RoleSpec.validate_fn(数据驱动,支持第三方扩展)
        spec = RoleRegistry.get(agent.role)
        if spec is None:
            raise ValueError(f"Unknown role: {agent.role}")
        spec.validate_fn(agent, depth, path)

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
                # 按 spec.is_subgraph 分派(替代硬编码 child.role == "worker")
                # 子图型 role(worker 等)直接 add_node;非子图型(supervisor 等)用
                # make_supervisor_node 包装。节点名前缀保持向后兼容:
                # worker_{name} / agent_{name}
                child_spec = RoleRegistry.get(child.role)
                if child_spec is None:
                    raise ValueError(f"Unknown role: {child.role}")
                if child_spec.is_subgraph:
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

        # leader_plan → step_gate 或直接路由(统一路由:运行时按 execution_mode 分派)
        # 路由函数接收 child_targets（logical→physical），返回物理节点名；
        # path_map 用 physical_targets（physical→destination，含 gate 重定向）。
        if has_step_gate:
            graph.add_edge("leader_plan", "step_gate")
            graph.add_conditional_edges(
                "step_gate",
                make_route_unified_to_worker(child_targets),
                physical_targets,
            )
        else:
            graph.add_conditional_edges(
                "leader_plan",
                make_route_unified(child_targets),
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

        # leader_review → step_gate 或直接路由(统一路由)
        if has_step_gate:
            graph.add_edge("leader_review", "step_gate")
        else:
            graph.add_conditional_edges(
                "leader_review",
                make_route_unified(child_targets),
                physical_targets,
            )

        return graph.compile(checkpointer=checkpointer)

    def _compile_worker(
        self, agent: Agent, default_model, trace_writer, audit_repo,
    ):
        """worker 沿用 make_worker_node（内部封装子图并剥离共享累加器字段，
        避免子图 reducer 与父图 reducer 双重累积）。

        SP7a:加载 agent.skills(缺失抛 KeyError,编译期 fail-fast)并透传给
        make_worker_node → make_init_worker,注入到 react_messages。
        """
        llm = self._mp.get_llm(agent.model or default_model)
        tools = self._tr.get_tools(agent.tools) if agent.tools else []
        skills = self._skill_loader.load(agent.skills)
        return make_worker_node(
            agent, llm, tools, trace_writer, audit_repo,
            run_manager=self._run_manager,
            skills=skills,
        )
