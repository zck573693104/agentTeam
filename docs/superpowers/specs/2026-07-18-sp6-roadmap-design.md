# SP6 演进路线图设计（P0-P4）

> 上游目标:打造企业级 Agent 专家团队,方便扩展 Agent 层级,挂载 MCP。
> SP1-SP5 已完成(统一 Agent 模型 + 多级 MCP + DB 持久化 + 运行时热更新 + 预置团队)。SP6 是基于架构评价报告的 5 项中期改进,补齐扩展性短板。

## 1. 目标与范围

### 1.1 目标
补齐架构评价报告中的 5 项扩展性短板,使架构可稳定支持 10+ 层嵌套、多 MCP 服务、多 provider、可中断可恢复、可并行编排的企业级场景。

### 1.2 范围
**包含 5 个独立子项目(按优先级):**
- **P0 Run 可恢复性**: 服务重启后 interrupted run 可通过 lazy recompile 续跑
- **P1 Plan DAG**: Plan 模型从线性 list 升级为 DAG,支持并行/条件分支
- **P2 ToolRegistry 缓存 key 修正**: 用 (name, command, args, transport, url) tuple 替代 server.name
- **P3 ModelProvider 注册表化**: 用 class-level registry 替代 if/elif 分派
- **P4 Run 取消机制**: 新增 `POST /api/runs/{id}/cancel` + threading.Event 协作取消

**不包含:**
- 持久化层迁移(Postgres/connection pool)— 单独立项
- asyncio 替换 threading — 风险过大,后续大版本
- Web UI 适配新 endpoint — 后续 M5b 迭代
- schema migration 框架 — 后续独立项目

### 1.3 优先级与依赖关系
```
P0 (Run 可恢复性)   ─┐
P2 (缓存 key)        ─┼─→ 互相独立,可并行
P3 (ModelProvider)   ─┘

P1 (Plan DAG)        ─→ 独立,但改动 state schema,需注意与 P0 的 checkpoint 兼容

P4 (Run 取消)        ─→ 依赖 P0(取消后状态需可恢复),建议在 P0 之后
```

**建议实施顺序**: P2 → P3 → P0 → P1 → P4
- P2/P3 是小改动,先清债
- P0 是最大可用性提升
- P1 改 state schema,放中间避免影响 P0 测试
- P4 依赖 P0,放最后

## 2. P0: Run 可恢复性

### 2.1 问题
`RunManager._graphs` / `_configs` 是内存态,服务重启后丢失。`resume_run` 抛 ValueError → approve_run 返回 409,interrupted run 永远卡死。

### 2.2 方案: lazy recompile on approve
- `runs` 表已有 `team_name` + `task` 字段(由 `run_repo.create_run` 写入)。
- `approve_run` 流程改造:
  1. `try_claim(interrupted→running)` (原子,已存在)
  2. 若 `run_manager._graphs[run_id]` 存在: 走原 `resume_run` 路径
  3. 若不存在: **lazy recompile**
     - 从 `team_store.get(run.team_name)` 取 Team
     - 用 `TeamCompiler` 重新 compile graph(注入 model_provider/tool_registry/library/checkpointer)
     - 注入到 `run_manager._graphs[run_id]` / `_configs[run_id]`
     - 调用 `resume_run(run_id, ...)`
- SqliteSaver 的 checkpoint 已持久化 interrupt 状态,`graph.invoke(Command(resume=...), config)` 能从 checkpoint 续跑。

### 2.3 接口变更
**新增 `RunManager.recompile_and_resume(run_id, team, compiler_factory, approved, reason)`**:
```python
def recompile_and_resume(
    self, run_id: str, team: Team,
    compiler_factory: Callable[[], TeamCompiler],  # 工厂,注入 mp/tr/lib
    approved: bool, reason: str | None = None,
) -> None:
    """lazy recompile graph + resume。供 approve_run 在 _graphs 缺失时调用。"""
    compiler = compiler_factory()
    # 注册所有 team 到 compiler._team_registry(使 TeamRef 可解析)
    # 注:这里需要 team_store.list_all(),由调用方在 compiler_factory 闭包中提供
    graph = compiler.compile(team, checkpointer=self._saver, ...)
    config = {"configurable": {"thread_id": run_id}}
    with self._lock:
        self._graphs[run_id] = graph
        self._configs[run_id] = config
    self.resume_run(run_id, approved, reason)
```

**`approve_run` 路由改造** (`api/routes/runs.py`):
```python
@router.post("/{run_id}/approve")
def approve_run(run_id: str, req: ApproveRequest):
    run = run_repo.get_run(run_id)
    if run is None: raise HTTPException(404, ...)
    if not run_repo.try_claim(run_id, "interrupted", "running"):
        raise HTTPException(400, ...)
    try:
        if run_manager.has_graph(run_id):
            run_manager.resume_run(run_id, req.approved, req.reason)
        else:
            # lazy recompile
            team = team_store.get(run["team_name"])
            if team is None:
                raise ValueError(f"Team '{run['team_name']}' not found (needed for recompile)")
            run_manager.recompile_and_resume(
                run_id, team,
                compiler_factory=lambda: _build_compiler(model_provider, tool_registry, agent_library, team_store),
                approved=req.approved, reason=req.reason,
            )
    except Exception as e:
        run_repo.end_run(run_id, "failed")
        # ... 错误事件发布(同现有 BUG-10 修复)
        status_code = 409 if isinstance(e, ValueError) else 500
        raise HTTPException(status_code=status_code, detail=str(e))
    return {"ok": True}
```

### 2.4 RunManager 需注入 checkpointer
当前 `RunManager.__init__(run_repo, audit_repo, event_bus)` 不持有 checkpointer。lazy recompile 需要它。改造为:
```python
def __init__(self, run_repo, audit_repo, event_bus, checkpointer=None):
    self._saver = checkpointer
    ...
```
`server.py` 创建 RunManager 时传入 `saver`。

### 2.5 测试覆盖
- `test_approve_after_restart_recompiles_and_resumes`: 模拟重启(清空 `_graphs`),approve 应 lazy recompile + resume
- `test_approve_after_restart_team_deleted_returns_409`: 重启后 team 也被删,approve 应 409 + 标 failed
- `test_approve_with_graph_present_uses_fast_path`: 不重启时走原路径,不触发 recompile
- `test_recompile_uses_correct_checkpointer`: 验证 recompile 后 graph 持有原 checkpointer,能从 checkpoint 续跑

## 3. P1: Plan DAG

### 3.1 问题
当前 `Plan = list[Step]`, `route_from_plan` 只看 `plan[0]`,无法表达并行/条件分支。

### 3.2 方案: Plan 升级为 DAG
**Step 模型扩展**:
```python
class PlanStep(BaseModel):
    worker: str
    instruction: str
    depends_on: list[str] = []      # 依赖的 step id(空=可立即执行)
    condition: str | None = None    # Python 表达式,求值 False 则跳过
    id: str = ""                    # 唯一 id(空=用 worker 名)
```

**Plan 模型扩展**:
```python
class Plan(BaseModel):
    steps: list[PlanStep]
    # 新增:执行模式
    execution_mode: Literal["sequential", "dag"] = "sequential"  # 向后兼容
```

**路由算法**:
- `sequential` 模式: 沿用 `route_from_review` 取 `plan[current_step]`(向后兼容)
- `dag` 模式: 拓扑排序,已完成的 step 的后继(依赖已满足)并行触发
  - `current_step` 字段在 dag 模式下不使用,改用 `completed_steps: set[str]`
  - `pending_steps: set[str]` = 所有 step id - completed_steps
  - `ready_steps: set[str]` = pending 中依赖已全部完成的
  - 路由: `ready_steps` 中的每个 worker 并行触发

**State schema 扩展**:
```python
class TeamState(TypedDict):
    # ... 现有字段 ...
    completed_steps: Annotated[set[str], set_union]   # dag 模式用
    skipped_steps: Annotated[set[str], set_union]      # condition 求值 False 跳过
```

**Leader plan 节点**:
- LLM structured output 仍输出 `Plan`,但 `execution_mode` 由 leader system prompt 决定(默认 sequential)
- dag 模式下 leader 输出含 `depends_on` / `condition` 的 steps

**新路由函数**:
```python
def make_route_from_plan_dag(child_targets: dict[str, str]):
    """dag 模式:返回 ready steps 的物理节点名列表(LangGraph 并行触发)"""
    def route(state: TeamState) -> list[str]:
        plan = state.get("plan", [])
        completed = state.get("completed_steps", set())
        skipped = state.get("skipped_steps", set())
        ready = []
        for step in plan:
            sid = step.get("id") or step.get("worker")
            if sid in completed or sid in skipped:
                continue
            deps = step.get("depends_on", [])
            if all(d in completed or d in skipped for d in deps):
                # 求值 condition(若存在)
                cond = step.get("condition")
                if cond and not _eval_condition(cond, state):
                    skipped.add(sid)  # 标记跳过
                    continue
                ready.append(child_targets[step["worker"]])
        return ready if ready else [END]
    return route
```

### 3.3 LangGraph 并行执行
LangGraph `add_conditional_edges` 返回 list 时,所有目标节点并行触发。worker 完成后回 `leader_review`,review 节点更新 `completed_steps`,再调 dag 路由看是否还有 ready。

**leader_review 改造**:
```python
def leader_review(state: TeamState) -> dict:
    # ... 现有逻辑 ...
    # dag 模式: 把刚完成的 step 加入 completed_steps
    if state.get("execution_mode") == "dag":
        just_done = state["plan"][?]  # 需要追踪当前完成的 step
        completed = set(state.get("completed_steps", set()))
        completed.add(just_done_id)
        return {"completed_steps": completed, ...}
    # sequential 模式: 沿用 current_step += 1
    return {"current_step": current + 1, ...}
```

**注意**: dag 模式下"当前完成的 step"需要从 worker 输出反推(worker_outputs 的 key 是 worker name,但同 worker 可能多步),因此 PlanStep 必须有唯一 `id`,worker 完成时回传 `completed_step_id`。

### 3.4 WorkerState 扩展
```python
class WorkerState(TypedDict):
    # ... 现有字段 ...
    current_step_id: str  # dag 模式: 当前执行的 step id
```
worker finalize 时回传 `{"completed_step_id": current_step_id, ...}`,supervisor 层 leader_review 据此更新 `completed_steps`。

### 3.5 向后兼容
- `execution_mode` 默认 `sequential`,旧测试不受影响
- `depends_on` 默认 `[]`, `condition` 默认 `None`, `id` 默认 `""`,旧 schema 仍可解析
- `route_from_plan` (旧) 保留,新代码用 `make_route_from_plan_dag`
- `current_step` 字段在 sequential 模式下仍用,dag 模式下不读不写(保留以兼容旧 checkpoint)

### 3.6 测试覆盖
- `test_plan_dag_parallel_execution`: A 无依赖,B 无依赖,C 依赖 A+B。A/B 应并行,C 等 A/B 都完成才执行
- `test_plan_dag_condition_skips_step`: condition 求值 False 的 step 被跳过,其后继(依赖它)也跳过
- `test_plan_sequential_backward_compat`: execution_mode=sequential(默认)时行为与旧版完全一致
- `test_plan_dag_diamond_pattern`: 菱形依赖(A→B,C→D)正确执行
- `test_plan_dag_cycle_detected_at_compile`: 编译期检测 dag 中的循环依赖,抛 ValueError

## 4. P2: ToolRegistry 缓存 key 修正

### 4.1 问题
`_loaded_servers: set[str]` 用 `server.name` 作 key。两个 MCPServer 同名但 command/args 不同时,第二个被错误跳过,工具不注册。

### 4.2 方案: 用配置 tuple 作 key
```python
def _server_cache_key(server: MCPServer) -> tuple:
    """生成 MCP server 的缓存 key。

    用 (name, command, args, transport, url) 唯一标识一个 MCP server 配置。
    同名但配置不同的 server 视为不同实例,分别 loader 调用。
    """
    return (
        server.name,
        server.command,
        tuple(server.args),
        server.transport,
        server.url,
    )
```

**ToolRegistry 改造**:
```python
class ToolRegistry:
    def __init__(self, ...):
        self._loaded_servers: set[tuple] = set()  # 改为 set[tuple]
        ...

    def register_mcp_tools(self, server: MCPServer) -> list[str]:
        key = _server_cache_key(server)
        if key in self._loaded_servers:
            prefix = f"mcp:{server.name}:"
            return [name for name in self._tools if name.startswith(prefix)]
        # ... loader 调用 ...
        self._loaded_servers.add(key)
        # ... 注册工具 ...
```

**注意**: 工具名前缀仍用 `mcp:{server.name}:`,因此同名不同配置的 server 注册的工具会冲突(第二个 server 的同名工具会被跳过)。这是预期行为 — 用户应避免同名 MCP server,若需多实例应改 server.name。

### 4.3 测试覆盖
- `test_same_name_different_command_both_loaded`: 两个 MCPServer(name="git", command 不同)都触发 loader
- `test_same_config_second_call_uses_cache`: 同一 MCPServer 二次调用跳过 loader
- `test_loader_failure_not_cached`: loader 抛异常时 key 不入缓存,允许重试
- `test_different_name_different_key`: 不同 name 的 server 独立缓存

## 5. P3: ModelProvider 注册表化

### 5.1 问题
`ModelProvider.get_llm` 是 4 个 `if ref.provider == "..."` 硬编码,加 provider 要改源码,违反开闭原则。

### 5.2 方案: class-level registry + adapter 注册
```python
class ModelProvider:
    _registry: dict[str, type[BaseAdapter]] = {}  # class-level

    @classmethod
    def register(cls, name: str, adapter_cls: type) -> None:
        """注册 adapter 类。第三方 provider 在 import 时调用此方法。"""
        if name in cls._registry:
            raise ValueError(f"Provider already registered: {name}")
        cls._registry[name] = adapter_cls

    @classmethod
    def list_providers(cls) -> list[str]:
        return list(cls._registry.keys())

    def get_llm(self, ref: ModelRef) -> BaseChatModel:
        adapter_cls = self._registry.get(ref.provider)
        if adapter_cls is None:
            raise ValueError(f"Unknown provider: {ref.provider}. Registered: {self.list_providers()}")
        return adapter_cls(self._api_keys).build(ref)
```

**BaseAdapter 协议**(新文件 `agentteam/models/adapters/base.py`):
```python
from abc import ABC, abstractmethod
from langchain_core.language_models import BaseChatModel
from agentteam.models.provider import ModelRef

class BaseAdapter(ABC):
    """Model provider adapter 协议。"""
    def __init__(self, api_keys: dict[str, str]):
        self._api_keys = api_keys

    @abstractmethod
    def build(self, ref: ModelRef) -> BaseChatModel:
        """构造 BaseChatModel 实例。"""
```

**内置 adapter 注册**(`agentteam/models/adapters/__init__.py`):
```python
from agentteam.models.provider import ModelProvider
from .qwen import QwenAdapter
from .openai_adapter import OpenAIAdapter
from .anthropic import AnthropicAdapter
from .ollama import OllamaAdapter

# 框架启动时自动注册内置 adapter
ModelProvider.register("qwen", QwenAdapter)
ModelProvider.register("openai", OpenAIAdapter)
ModelProvider.register("anthropic", AnthropicAdapter)
ModelProvider.register("ollama", OllamaAdapter)
```

**ModelProvider.__init__ 触发 adapter 包 import**:
```python
class ModelProvider:
    def __init__(self, api_keys=None):
        self._api_keys = api_keys or {}
        # 触发 adapter 注册(import 副作用)
        from agentteam.models import adapters  # noqa: F401
```

### 5.3 第三方注册示例
```python
# my_company/custom_adapter.py
from agentteam.models.adapters.base import BaseAdapter
from agentteam.models.provider import ModelProvider, ModelRef

class MyAdapter(BaseAdapter):
    def build(self, ref: ModelRef) -> BaseChatModel:
        return MyChatModel(...)

ModelProvider.register("my_company", MyAdapter)
```

### 5.4 测试覆盖
- `test_register_custom_provider`: 注册新 adapter,`get_llm` 能解析
- `test_register_duplicate_raises`: 重名注册抛 ValueError
- `test_get_llm_unknown_provider_lists_registered`: 未知 provider 错误信息列出已注册
- `test_builtin_providers_registered_by_default`: 框架启动后 qwen/openai/anthropic/ollama 自动可用
- `test_list_providers_returns_all`: list_providers 返回所有已注册

## 6. P4: Run 取消机制

### 6.1 问题
后台线程是 daemon,run 一旦启动无法中止。长 run 浪费 LLM token,用户无能为力。

### 6.2 方案: threading.Event 协作取消
**RunManager 新增 cancel event**:
```python
class RunManager:
    def __init__(self, ...):
        self._cancel_events: dict[str, threading.Event] = {}
        ...

    def start_run(self, run_id, graph, config, task):
        self._cancel_events[run_id] = threading.Event()
        # ... 原有逻辑 ...

    def cancel_run(self, run_id) -> bool:
        """请求取消 run。返回是否成功发出取消信号。"""
        with self._lock:
            event = self._cancel_events.get(run_id)
            if event is None:
                return False  # run 不存在或已完成
            event.set()
        # 标记 run 为 cancelling(中间态,worker 检测到 event 后抛 CancelledError,
        # _handle_error 会标 failed)
        self._run_repo.update_status(run_id, "cancelling")
        return True

    def is_cancelled(self, run_id) -> bool:
        """供 worker 节点轮询检查。"""
        event = self._cancel_events.get(run_id)
        return event is not None and event.is_set()

    def _cleanup_run(self, run_id):
        # ... 原有逻辑 ...
        self._cancel_events.pop(run_id, None)
```

**worker 节点检查取消**:
```python
def make_agent_step(agent, llm, tools, run_manager=None):
    """新增 run_manager 参数,用于检查取消信号。"""
    def agent_step(state: dict) -> dict:
        if run_manager is not None:
            run_id = state.get("run_id", "")
            if run_manager.is_cancelled(run_id):
                raise RunCancelledError(f"Run {run_id} cancelled by user")
        # ... 原有 LLM 调用 ...
    return agent_step
```

**新增异常 + 处理**:
```python
class RunCancelledError(Exception):
    """run 被用户取消。"""
    pass

# RunManager._handle_error 区分:
def _handle_error(self, run_id, error):
    if isinstance(error, RunCancelledError):
        self._run_repo.end_run(run_id, "cancelled")
        eid = self._audit_repo.add_event(run_id, "run_cancelled", "user")
        self._bus.publish(run_id, {"id": eid, "event_type": "run_cancelled", "run_id": run_id})
    else:
        # ... 原有 failed 逻辑 ...
    self._cleanup_run(run_id)
```

**新 API endpoint**:
```python
@router.post("/{run_id}/cancel")
def cancel_run(run_id: str):
    run = run_repo.get_run(run_id)
    if run is None:
        raise HTTPException(404, ...)
    if run["status"] not in ("running", "interrupted"):
        raise HTTPException(400, detail=f"Cannot cancel run in status: {run['status']}")
    if not run_manager.cancel_run(run_id):
        raise HTTPException(409, detail="Run not active or already cancelled")
    return {"ok": True}
```

**runs 表 status 新增 "cancelling" / "cancelled" 状态**:
- `cancelling`: cancel 信号已发,worker 尚未检测到
- `cancelled`: worker 已抛 CancelledError,run 已结束

### 6.3 依赖 P0
若 run 处于 `interrupted` 状态被 cancel,需先 lazy recompile(P0)才能让 worker 检测取消信号。但更简单: `interrupted` 状态直接标 `cancelled`(无需 recompile),因为 run 已暂停,直接结束即可。

**简化方案**:
```python
def cancel_run(self, run_id) -> bool:
    run = self._run_repo.get_run(run_id)
    if run is None: return False
    if run["status"] == "interrupted":
        # interrupted run 直接结束,无需 recompile
        self._run_repo.end_run(run_id, "cancelled")
        self._cleanup_run(run_id)
        return True
    # running 状态: 设置 event,等 worker 检测
    event = self._cancel_events.get(run_id)
    if event is None: return False
    event.set()
    self._run_repo.update_status(run_id, "cancelling")
    return True
```

### 6.4 测试覆盖
- `test_cancel_running_run_sets_event`: running run cancel 后 worker 检测到 event 抛 CancelledError
- `test_cancel_interrupted_run_ends_directly`: interrupted run cancel 直接标 cancelled,不 recompile
- `test_cancel_completed_run_returns_409`: 已完成的 run cancel 返回 409
- `test_cancel_emits_run_cancelled_event`: cancel 后 EventBus 收到 run_cancelled 事件
- `test_worker_checks_cancel_between_iterations`: worker 在 agent_step 入口检查 cancel,不浪费 LLM 调用

## 7. 风险与缓解

| 风险 | 缓解 |
|------|------|
| P0 lazy recompile 与 SP4 热更新冲突 | compiler_factory 在 approve_run 内构造,不复用旧 compiler 实例 |
| P1 state schema 变更破坏旧 checkpoint | `completed_steps` 字段默认空 set,旧 checkpoint 反序列化时缺失则视为空 |
| P2 缓存 key 变更导致现有测试失败 | 测试用 fake loader,不依赖真实 MCP server,只需更新 cache 断言 |
| P3 import 副作用可能产生循环依赖 | adapter 包 import 在 ModelProvider.__init__ 内延迟触发,不在模块顶层 |
| P4 CancelledError 可能被 worker 内部 try/except 吞掉 | RunCancelledError 继承 BaseException 而非 Exception,绕过常规 catch |

## 8. 验收标准

每个子项目独立验收,均需:
1. 所有新增测试通过
2. 全量回归测试通过(目标: 418 → 440+ tests)
3. 工作树 clean,每个子项目独立 commit
4. 文档更新(本 spec + 对应 sub-plan)

## 9. 实施顺序与依赖

```
Phase 1: P2 (ToolRegistry)        ─ 独立,小改动,先清债
Phase 2: P3 (ModelProvider)       ─ 独立,小改动,清债
Phase 3: P0 (Run 可恢复性)         ─ 独立,大改动,最大可用性提升
Phase 4: P1 (Plan DAG)            ─ 独立,大改动,注意 state schema 兼容
Phase 5: P4 (Run 取消)             ─ 依赖 P0(简化后实际不依赖),大改动
```

每个 Phase 完成后做一次全量回归 + commit,确保可回滚。
