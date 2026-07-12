# M5a —— FastAPI 后端 API 设计

> 日期：2026-07-12
> 状态：已通过设计评审，待实现
> 前置：M1-M4 已完成（基础设施 + 领域编译 + 审批轨迹 + MCP 集成）

## 1. 背景与目标

M5 拆为 M5a（后端 API）+ M5b（前端 Web UI）。本 spec 只覆盖 M5a。

M5a 的目标：用 FastAPI + SSE 把已有的 TeamCompiler / RunRepo / AuditRepo / TraceWriter 包装成 RESTful API，支持：

- 团队定义的注册与查询（JSON RESTful）
- 提交任务、后台异步执行 run
- SSE 实时推送执行事件
- 查询完整执行轨迹（回放）
- 审批决策提交 + 自动续跑
- 用量统计仪表盘数据

## 2. 架构总览

### 2.1 分层

```
┌──────────────────────────────────────────────────┐
│  FastAPI Server (agentteam/api/server.py)         │
│  ├── routes/teams.py    — 团队 CRUD               │
│  ├── routes/runs.py     — 运行 + SSE + 审批        │
│  └── routes/dashboard.py — 用量统计                │
├──────────────────────────────────────────────────┤
│  Service Layer                                    │
│  ├── TeamStore    — 团队注册表（内存 dict）        │
│  ├── RunManager   — 后台线程 + interrupt/resume    │
│  └── EventBus     — 线程安全事件队列               │
├──────────────────────────────────────────────────┤
│  Existing Runtime (不改动)                         │
│  ├── TeamCompiler  — Team → StateGraph            │
│  ├── TraceWriter   — SqliteTraceWriter             │
│  └── AuditRepo / RunRepo — SQLite 存储             │
└──────────────────────────────────────────────────┘
```

### 2.2 运行执行模型

方案 A（后台线程 + 线程安全事件队列）：

- `POST /api/runs` 在后台 `threading.Thread` 中跑 `graph.invoke()`，立即返回 run_id
- `BroadcastTraceWriter` 双写：SQLite（持久）+ EventBus（实时）
- SSE 端点订阅 EventBus 队列，逐条 yield 事件
- run 执行与 SSE 连接解耦——关浏览器 run 继续跑
- interrupt 暂停时，后台线程的 `invoke()` 返回，RunManager 发布 `run_interrupted` 控制事件

## 3. 核心组件

### 3.1 TeamSerializer (`api/serializer.py`)

Team dataclass 与 JSON 之间的双向转换。

```python
def team_to_dict(team: Team) -> dict:
    """Team dataclass → JSON-serializable dict（用 dataclasses.asdict）。"""

def team_from_dict(data: dict) -> Team:
    """dict → Team，手动重建嵌套 Leader/Worker/ApprovalPolicy/MCPServer/ModelRef。"""
```

`team_from_dict` 需要按字段类型感知地重建嵌套对象：
- `leader` → `Leader(**dict)`，其中 `model` → `ModelRef(**dict)`，`approval_policy` → `ApprovalPolicy(**dict)` 或 None
- `workers` → `list[Worker]`，每个 Worker 同理重建
- `default_model` → `ModelRef(**dict)`
- `mcp_servers` → `list[MCPServer]`
- `skills` → `list[str]`（直接用）

### 3.2 TeamStore (`api/store.py`)

内存团队注册表，按 `team.name` 索引。不持久化（重启后重新注册）。

```python
class TeamStore:
    def register(self, team: Team) -> None: ...
    def get(self, name: str) -> Team | None: ...
    def list_all(self) -> list[Team]: ...
    def delete(self, name: str) -> bool: ...
```

### 3.3 EventBus (`api/events.py`)

线程安全事件总线，桥接 TraceWriter → SSE。

```python
class EventBus:
    def subscribe(self, run_id: str) -> queue.Queue: ...
    def publish(self, run_id: str, event: dict) -> None: ...
    def unsubscribe(self, run_id: str, q: queue.Queue) -> None: ...
```

- 内部：`dict[run_id → list[Queue]]`，配 `threading.Lock`
- `subscribe`：为 run_id 创建/追加一个 `queue.Queue`，返回引用
- `publish`：遍历该 run_id 的所有 Queue，`put_nowait(event)`
- `unsubscribe`：从列表中移除 Queue，列表空时清理 run_id 键
- 支持多个 SSE 客户端订阅同一 run

### 3.4 BroadcastTraceWriter (`api/events.py`)

实现 TraceWriter 协议（`emit()` 方法），双写 SQLite + EventBus。

```python
class BroadcastTraceWriter:
    def __init__(self, audit_repo: AuditRepo, bus: EventBus): ...
    def emit(self, run_id, event_type, actor, payload=None, duration_ms=None, tokens=None) -> None:
        event_id = self._audit_repo.add_event(run_id, event_type, actor, payload, duration_ms, tokens)
        self._bus.publish(run_id, {
            "id": event_id,  # SQLite 行 ID，供 SSE 回放去重
            "run_id": run_id, "event_type": event_type, "actor": actor,
            "payload": payload, "duration_ms": duration_ms, "tokens": tokens,
        })
```

关键：`AuditRepo.add_event()` 返回 SQLite 行 ID（`cur.lastrowid`），`emit()` 捕获它并放入发布到 EventBus 的事件 dict 中。SSE 回放用此 ID 去重。

编译时注入 TeamCompiler，替换裸 SqliteTraceWriter。

### 3.5 RunManager (`api/run_manager.py`)

管理后台线程执行 + interrupt/resume。

```python
class RunManager:
    def __init__(self, run_repo, audit_repo, event_bus): ...

    def start_run(self, run_id: str, graph, config: dict, task: str) -> None:
        """在 threading.Thread 中跑 graph.invoke()，立即返回。"""

    def resume_run(self, run_id: str, approved: bool, reason: str | None) -> None:
        """用 Command(resume=...) 启新线程续跑。"""

    def get_pending_approval(self, run_id: str, graph, config: dict) -> dict | None:
        """从 graph.get_state(config) 读 pending_approval。"""
```

内部追踪：
- `dict[run_id → Thread]`：活跃后台线程
- `dict[run_id → graph]`：编译后的图实例（resume 时复用）
- `dict[run_id → config]`：含 thread_id 的 config（resume 时复用）

**后台线程执行逻辑：**

```python
def _run_in_background(self, run_id, graph, config, task):
    try:
        self._audit_repo.add_event(run_id, "run_start", "system", {"task": task})
        graph.invoke({"task": task}, config)
        state = graph.get_state(config)
        if state.next:  # 还有节点 = 被 interrupt 暂停
            self._run_repo.update_status(run_id, "interrupted")
            # run_interrupted 是控制信号，只推 EventBus（不写 SQLite）
            self._event_bus.publish(run_id, {"event_type": "run_interrupted",
                                             "run_id": run_id})
        else:
            self._run_repo.end_run(run_id, "completed")
            eid = self._audit_repo.add_event(run_id, "run_end", "system")
            self._event_bus.publish(run_id, {"id": eid, "event_type": "run_end",
                                             "run_id": run_id})
    except Exception as e:
        self._run_repo.end_run(run_id, "failed")
        eid = self._audit_repo.add_event(run_id, "error", "system", {"error": str(e)})
        self._event_bus.publish(run_id, {"id": eid, "event_type": "error",
                                         "run_id": run_id, "payload": {"error": str(e)}})
```

关键区分：
- `run_start` / `run_end` / `error`：写 SQLite（持久轨迹）+ 推 EventBus（实时）
- `run_interrupted`：纯控制信号，只推 EventBus（interrupt 状态已由 LangGraph checkpointer + AuditRepo approval 记录）

## 4. API 端点

### 4.1 端点清单

| 方法 | 端点 | Body | 响应 | 说明 |
|---|---|---|---|---|
| GET | `/api/teams` | — | `Team[]` | 列出所有注册团队 |
| POST | `/api/teams` | `Team JSON` | `{name}` | 注册团队 |
| GET | `/api/teams/{name}` | — | `Team` | 获取团队详情 |
| DELETE | `/api/teams/{name}` | — | `{ok}` | 删除团队 |
| POST | `/api/runs` | `{team_name, task}` | `{run_id}` | 提交任务，启动 run |
| GET | `/api/runs` | — | `Run[]` | 列出所有 run |
| GET | `/api/runs/{id}` | — | `Run` | 获取 run 状态 |
| GET | `/api/runs/{id}/stream` | — | `text/event-stream` | SSE 实时事件流 |
| GET | `/api/runs/{id}/trace` | — | `AuditEvent[]` | 完整执行轨迹（读 SQLite） |
| GET | `/api/runs/{id}/approvals` | — | `Approval[]` | 列出 run 的审批记录 |
| POST | `/api/runs/{id}/approvals/{aid}` | `{approved, reason?}` | `{ok}` | 提交审批决策 + 自动续跑 |
| GET | `/api/dashboard` | — | `DashboardStats` | 用量统计 |

`DashboardStats` 响应格式：

```json
{
  "total_runs": 42,
  "total_tokens": 125000,
  "by_status": {"completed": 35, "failed": 3, "interrupted": 2, "running": 2},
  "by_team": {"dev": 30, "research": 12},
  "recent_runs": [{"run_id": "abc", "team_name": "dev", "task": "...", "status": "completed"}]
}
```

### 4.2 设计决策：合并 /resume

原设计的 `POST /api/runs/{id}/resume` 合并进 `POST /api/runs/{id}/approvals/{aid}`。原因：本框架所有 interrupt 都是审批触发（step 级 / worker 级 / tool 级），审批端点既记录决策又用 `Command(resume=...)` 续跑，避免语义重复。

### 4.3 Team JSON 格式

```json
{
  "name": "dev",
  "description": "研发小队",
  "leader": {
    "name": "leader",
    "role": "主管",
    "system_prompt": "你是主管",
    "model": {"provider": "qwen", "name": "qwen-max"},
    "approval_policy": null
  },
  "workers": [
    {
      "name": "coder",
      "role": "代码工程师",
      "description": "写代码",
      "system_prompt": "你是代码工程师",
      "model": null,
      "tools": ["read_file", "write_file"],
      "approval_policy": {"level": "tool", "targets": ["write_file"]},
      "max_iterations": 10
    }
  ],
  "default_model": {"provider": "qwen", "name": "qwen-max"},
  "skills": [],
  "mcp_servers": []
}
```

- `model` 为 null 时 fallback 到 `default_model`
- `approval_policy` 为 null 表示无审批
- `mcp_servers` 每项含 `name/command/args/env/transport/url`

## 5. SSE 事件协议

### 5.1 事件格式

标准 SSE 格式：

```
event: worker_start
data: {"run_id":"abc","actor":"coder","timestamp":"2026-07-12T..."}

```

### 5.2 事件类型

对齐 AuditEvent.event_type + 1 个控制事件：

| 事件 | 说明 |
|---|---|
| `run_start` | run 开始 |
| `leader_plan` | Leader 拆解计划 |
| `worker_start` | Worker 开始执行 |
| `tool_call` | 工具调用（含工具名、参数、结果） |
| `approval_requested` | 审批请求（含 approval_id） |
| `worker_end` | Worker 结束 |
| `approval_decided` | 审批决策已提交 |
| `run_end` | run 正常结束 |
| `error` | 执行错误 |
| `run_interrupted` | **控制事件**：图在 interrupt 暂停，等待审批 |

`run_interrupted` 是非 AuditEvent 的控制信号——后台线程的 `invoke()` 返回时由 RunManager 发布，通知 UI「该弹审批框了」。payload 含 `run_id` 及从 `graph.get_state(config).values["pending_approval"]` 读出的审批上下文（level / actor / reason）。

### 5.3 SSE 回放策略（"先回放再直播"）

客户端连接 `/api/runs/{id}/stream` 时：

1. **先订阅 EventBus**（队列开始缓冲，防止 race condition 丢事件）
2. **读 SQLite 历史事件**（`AuditRepo.list_events`），逐条发送，记录 `last_event_id`
3. **切到直播模式**：从队列读事件，**跳过 `id <= last_event_id` 的**（去重）
4. 如果 run 已结束（无 live 事件），发完历史后关闭流
5. 客户端断开 → `EventBus.unsubscribe`

这样页面刷新不会丢历史，也不会因 race condition 丢新事件。

## 6. 数据流

### 6.1 提交任务

```
Client POST /api/runs {team_name, task}
  → TeamStore.get(team_name) → Team
  → RunRepo.create_run(team_name, task) → run_id
  → TeamCompiler.compile(team, checkpointer, BroadcastTraceWriter, audit_repo)
  → RunManager.start_run(run_id, graph, config, task)  // 启后台线程
  → return {run_id}  // 立即返回
```

### 6.2 SSE 实时推送

```
Client GET /api/runs/{id}/stream
  → EventBus.subscribe(run_id) → Queue  (先订阅，防丢)
  → AuditRepo.list_events(run_id) → 历史事件逐条 SSE 发送
  → 后台 graph 执行 → BroadcastTraceWriter.write()
    → SqliteTraceWriter.write()  // 持久
    → EventBus.publish()         // 实时推到 Queue
  → SSE 端点从 Queue yield 事件（跳过已回放的 id）
  → 图在 interrupt 暂停 → RunManager 发 run_interrupted
  → Client 断开 → EventBus.unsubscribe
```

### 6.3 审批续跑

```
Client POST /api/runs/{id}/approvals/{aid} {approved: true}
  → AuditRepo.decide_approval(aid, "approved", "user", reason)
  → RunManager.resume_run(run_id, approved)  // Command(resume=...) 启新线程
  → return {ok}
  // 后台续跑 → 事件继续推到 EventBus → 已订阅的 SSE 客户端自动收到
```

## 7. 错误处理

### 7.1 HTTP 错误码

| 场景 | 状态码 | 说明 |
|---|---|---|
| 团队不存在 | 404 | `GET/DELETE /api/teams/{name}`、`POST /api/runs` 时 |
| Run 不存在 | 404 | `GET /api/runs/{id}/*` |
| 团队 JSON 格式错误 | 422 | FastAPI/Pydantic 自动校验 + TeamSerializer 手动校验 |
| Run 已结束仍尝试审批 | 400 | `POST /approvals/{aid}` 时 run 状态非 `interrupted` |
| 无 pending approval | 400 | RunManager 检测无 interrupt |
| MCP 加载失败 | 400 | compile 时异常，返回给 `POST /api/runs` |
| 图执行异常 | 200 + `error` SSE 事件 | 后台线程 catch → run 状态 `failed` → 发 error 事件 |

### 7.2 后台线程错误处理

后台线程内 catch 所有异常：
- run 状态置 `failed`（`RunRepo.end_run`）
- 发 `error` SSE 事件（含错误信息）
- 不影响 API 进程

## 8. FastAPI App 工厂

```python
def create_app(db_path="data/agentteam.db") -> FastAPI:
    app = FastAPI(title="AgentTeam")

    # 单例服务
    conn = init_db(db_path)
    run_repo = RunRepo(conn)
    audit_repo = AuditRepo(conn)
    team_store = TeamStore()
    event_bus = EventBus()
    run_manager = RunManager(run_repo, audit_repo, event_bus)
    model_provider = ModelProvider()
    tool_registry = ToolRegistry()

    # 注入路由
    app.include_router(teams_router(team_store))
    app.include_router(runs_router(run_manager, team_store, model_provider,
                                   tool_registry, run_repo, audit_repo, event_bus))
    app.include_router(dashboard_router(run_repo, audit_repo))

    return app
```

启动：`uvicorn agentteam.api.server:create_app --factory`

## 9. 测试策略

### 9.1 单元测试（无 FastAPI）

- `test_serializer.py`：Team ↔ JSON 双向转换（含嵌套 ApprovalPolicy/MCPServer/ModelRef）
- `test_store.py`：TeamStore CRUD
- `test_events.py`：EventBus pub/sub（多订阅者、unsubscribe 清理）
- `test_run_manager.py`：用 FakeLLM 跑后台线程，验证状态流转（running → interrupted / completed / failed）

### 9.2 API 集成测试（FastAPI TestClient + FakeLLM + 内存 SQLite）

- `test_api_teams.py`：注册/列表/获取/删除团队
- `test_api_runs.py`：提交任务 → 获取状态 → 查轨迹
- `test_api_approvals.py`：提交任务 → interrupt → 审批续跑 → 完成
- `test_api_dashboard.py`：用量统计

### 9.3 SSE 测试

- TestClient 对 `/stream` 发请求，用 FakeLLM 让 run 快速完成，解析响应中的 SSE 事件序列
- 验证事件顺序：`run_start → leader_plan → worker_start → ... → run_end`
- 验证回放：先跑完 run，再连 SSE，应收到全部历史事件后关闭

## 10. 新增依赖

`pyproject.toml` 添加：

```
fastapi>=0.115
uvicorn>=0.30
sse-starlette>=2.0
```

`dev` 依赖加 `httpx>=0.27`（FastAPI TestClient 底层依赖）。

## 11. 项目结构

```
agentteam/api/
├── __init__.py
├── server.py          # FastAPI app 工厂 (create_app)
├── serializer.py      # Team JSON ↔ dataclass
├── store.py           # TeamStore
├── events.py          # EventBus + BroadcastTraceWriter
├── run_manager.py     # RunManager
└── routes/
    ├── __init__.py
    ├── teams.py       # /api/teams/*
    ├── runs.py        # /api/runs/*
    └── dashboard.py   # /api/dashboard
```

## 12. 非目标（M5a 不做）

- Web UI 前端（M5b）
- 团队持久化到 SQLite（内存即可，重启重新注册）
- 用户认证 / RBAC
- WebSocket（SSE 足矣）
- 多 run 并发限制 / 队列（本地工具，同时跑几个 run 可接受）
