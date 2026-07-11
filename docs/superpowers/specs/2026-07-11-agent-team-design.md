# AgentTeam —— 本地多智能体协作框架设计

> 日期：2026-07-11
> 状态：已通过设计评审，待实现
> 参考来源：阿里云 AgentTeams（多智能体治理与协作平台）+ AgentLoop（智能体观测优化平台）

## 1. 背景与目标

### 1.1 背景

学习阿里云 AgentTeams（企业级多智能体治理与协作平台）与 AgentLoop（智能体观测优化平台）的产品理念，构建一套**个人/小团队可本地运行**的轻量级多智能体协作框架（迷你版 AgentTeams）。

AgentTeams 的核心理念：
- **Leader-Worker 分工架构**：Leader Agent 负责理解任务、拆解步骤、分配给 Worker Agent 执行
- **角色说明书**：明确每个智能体的职责边界
- **审批流程**：关键节点必须经过人类确认
- **工作群可见性**：人类可实时看到 Agent 之间的交流
- **统一模型管理**：接入多供应商模型，灵活切换
- **MCP 服务 + Skills**：扩展工具调用能力
- **全链路审计**：调用记录、资源消耗、运行成本可审计

AgentLoop 的核心理念：
- 执行轨迹自动记录（思考过程、工具调用、资源消耗）
- 观测 → 评估 → 优化 → 再观测的进化闭环
- Agent-as-a-Judge 评估范式

### 1.2 目标

构建一个本地多智能体框架，具备：
- Leader-Worker 编排（基于 LangGraph）
- 工具调用（MCP + 原生 Skill 双轨）
- 声明式审批点（Human-in-the-loop）
- 执行轨迹记录与回放（对标 AgentLoop 本地版）
- 多供应商模型抽象（默认 Qwen）
- FastAPI + Web UI 控制台

### 1.3 非目标（v1 不做）

- 多租户隔离
- RBAC 权限体系
- IM 集成（钉钉/飞书）
- 企业级合规（密钥集中托管、审计合规）
- Agent-as-a-Judge 评估器实现（仅预留接口）
- 异构 Agent 纳管（OpenClaw/Claude Code 等）

## 2. 总体架构

### 2.1 分层架构

```
┌──────────────────────────────────────────────────────────┐
│  Web UI 控制台   团队管理 / 任务提交 / 实时看板 / 轨迹回放 / 审批待办  │
├──────────────────────────────────────────────────────────┤
│  FastAPI 层     REST 端点 + SSE 流式推送                  │
├──────────────────────────────────────────────────────────┤
│  领域层 (AgentTeams 概念映射)                              │
│    Team · Worker(角色说明书) · Leader · Skill · MCPServer  │
│    · ApprovalPolicy · AuditLog/Trace                      │
├──────────────────────────────────────────────────────────┤
│  执行内核 (LangGraph)                                     │
│    StateGraph 编译 · Supervisor · ReAct · interrupt       │
│    · SqliteSaver checkpoint · 流式                         │
├──────────────────────────────────────────────────────────┤
│  基础设施层                                                │
│    ModelProvider(Qwen/OpenAI/Anthropic/Ollama)            │
│    · ToolRegistry(原生 Skill + MCP) · SQLite · 日志        │
└──────────────────────────────────────────────────────────┘
```

### 2.2 设计原则

- **概念与执行分离**：领域层只描述「AgentTeams 是什么」（概念模型 + 配置），不含执行细节；执行细节全交给 LangGraph 内核。
- **配置驱动**：一个 Team 配置（YAML/Python）→ `TeamCompiler` → 编译成可执行的 `StateGraph`。
- **边界清晰可测试**：每层有明确职责，可独立测试；可替换内核而不影响领域模型。
- **YAGNI**：v1 聚焦核心能力，企业级特性留接口不实现。

## 3. 领域模型

### 3.1 概念映射

| AgentTeams 概念 | 领域对象 | 说明 |
|---|---|---|
| Team（团队） | `Team` | 一个 Leader + N 个 Worker + 工具集 + 审批策略；编译成一个图 |
| 主管智能体 | `Leader` | Supervisor 节点：理解任务→拆步骤→分发给 Worker→汇总 |
| 角色说明书 | `Worker` | `name/role/system_prompt/model/tools/approval_policy`，执行 ReAct |
| Skills | `Skill` | 原生 Python `@tool`，有 name/description/schema |
| MCP 服务 | `MCPServer` | 外部工具服务配置（command/args/env），运行时拉起子进程加载工具 |
| 审批流程 | `ApprovalPolicy` | 声明式规则：worker 级 / tool 级 / step 级，用 `interrupt()` 实现 |
| 执行轨迹/审计 | `Trace` / `AuditEvent` | run 事件流落 SQLite，对标 AgentLoop |
| 统一模型管理 | `ModelProvider` | 多供应商抽象，Worker 配置里引用 |

### 3.2 核心数据结构

**Worker（角色说明书）**：

```python
@dataclass
class Worker:
    name: str                       # 唯一标识，如 "coder"
    role: str                       # 角色名，如 "代码工程师"
    description: str                # 职责描述
    system_prompt: str              # 系统提示词
    model: ModelRef                 # 模型引用
    tools: list[str]                # 可用工具名（Skill 名 + "mcp:" 前缀）
    approval_policy: ApprovalPolicy # 审批策略
    max_iterations: int = 10        # ReAct 最大循环次数
```

**Leader**：

```python
@dataclass
class Leader:
    name: str = "leader"
    role: str = "主管"
    system_prompt: str              # 任务拆解与分配的提示词
    model: ModelRef
    approval_policy: ApprovalPolicy | None = None  # step 级审批（可选）
```

**Team**：

```python
@dataclass
class Team:
    name: str
    description: str
    leader: Leader
    workers: list[Worker]
    skills: list[Skill]             # 团队级原生工具
    mcp_servers: list[MCPServer]    # 团队级 MCP 服务
    default_model: ModelRef         # 全局默认模型
```

**ApprovalPolicy（声明式审批）三种粒度**：

```python
@dataclass
class ApprovalPolicy:
    level: Literal["worker", "tool", "step"]
    targets: list[str] | None = None
    # worker 级：targets 为 worker name 列表
    # tool 级：targets 为工具名列表（如 ["write_file"]）
    # step 级：targets 为 None（每步都审批）
    timeout_seconds: int | None = None  # None 表示无限等待
```

## 4. 编译执行（Team → LangGraph StateGraph）

### 4.1 TeamCompiler

`TeamCompiler` 把一个 `Team` 配置编译成可执行的 `StateGraph`。

### 4.2 State schema

```python
class Step(TypedDict):
    worker: str          # 指派的 worker name
    instruction: str     # 子任务描述
    status: str          # pending | running | done | failed

class TeamState(TypedDict):
    messages: Annotated[list, add_messages]   # 对话历史（LangGraph 标准）
    task: str                                  # 原始用户任务
    plan: list[Step]                           # Leader 拆解的计划
    current_step: int                          # 当前步骤索引
    worker_outputs: dict[str, str]             # 各 Worker 产出
    pending_approval: Optional[Approval]       # 待审批项
    audit_events: Annotated[list, append_only] # 轨迹事件累积
```

### 4.3 节点与边

```
START → leader_plan
leader_plan → [approval_gate?] → worker_{assigned}
worker_{name} → leader_review
leader_review → worker_{next} | END
```

- **`leader_plan`**：Leader 用 LLM + 结构化输出把 `task` 拆成 `plan`（每步指派一个 worker + 子任务描述）
- **`worker_{name}`**：每个 Worker 一个节点，内部跑 ReAct 循环（LLM 调工具直到完成），产出写入 `worker_outputs`
- **`leader_review`**：Leader 看 `worker_outputs`，决定派下一步还是收尾
- **`approval_gate`**：按 `ApprovalPolicy` 在节点前/后插 `interrupt()`，触发时 run 暂停

### 4.4 Worker 内部 ReAct 循环

每个 Worker 节点内部是一个 ReAct 子图：
1. LLM 接收当前 instruction + 历史 → 决定调用工具或给出最终答案
2. 若调用工具 → 执行工具（可能触发 tool 级审批 interrupt）→ 结果回灌 → 回到 1
3. 若给出最终答案 → 写入 `worker_outputs`，节点结束
4. 达到 `max_iterations` 强制结束

## 5. 工具层（ToolRegistry 统一注册）

### 5.1 ToolRegistry

全局注册表。Worker 配置里引用工具名，运行时从 registry 取并绑定到该 Worker 的 LLM。

```python
class ToolRegistry:
    def register_skill(self, skill: Skill) -> None: ...
    def register_mcp_tools(self, server: MCPServer) -> None: ...
    def get_tools(self, names: list[str]) -> list[BaseTool]: ...
```

### 5.2 原生 Skill

Python 函数 + `@tool` 装饰器，自动转 LangChain Tool。内置：
- `read_file`：读文件
- `write_file`：写文件
- `search_web`：网页搜索
- `list_dir`：列目录

每个 Skill 有 name/description/args_schema。

### 5.3 MCP 集成

`MCPServer(command, args, env)` → `StdioServerParameters` → `ClientSession` → `load_mcp_tools()` → 转 LangChain Tool 注册进 registry（命名加 `mcp:` 前缀防冲突）。Worker 可混用原生与 MCP 两类工具。

**生命周期**：MCP 子进程在 run 开始时拉起、结束时优雅关闭；崩溃可重启。

## 6. 模型层（ModelProvider 多供应商抽象）

```python
@dataclass
class ModelRef:
    provider: Literal["qwen", "openai", "anthropic", "ollama"]
    name: str               # 如 "qwen-max"
    temperature: float = 0.7
    # 其他参数

class ModelProvider:
    def get_llm(self, ref: ModelRef) -> BaseChatModel: ...
```

### 6.1 适配器

- `QwenAdapter`：dashscope `ChatTongyi` 或 OpenAI 兼容模式
- `OpenAIAdapter`：`ChatOpenAI`
- `AnthropicAdapter`：`ChatAnthropic`
- `OllamaAdapter`：`ChatOllama`

### 6.2 用量统计

每次 LLM 调用的 token / 耗时写入 `audit_events`，做用量统计（对标监控仪表盘）。

## 7. 持久化与观测

### 7.1 存储

单文件 `data/agentteam.db`，含两类存储：

**① LangGraph 原生**：`SqliteSaver` 做 checkpoint——状态快照、断点续跑、运行恢复全交给它。

**② 领域层结构化轨迹**（对标 AgentLoop）：

| 表 | 内容 |
|---|---|
| `runs` | run 元信息（team、task、状态、起止时间、token 总量） |
| `run_events` | 轨迹事件流（leader_plan / worker_start / tool_call / approval / error，含时间戳、耗时、token） |
| `approvals` | 审批记录（待办/已批/已拒、决策人、时间） |

### 7.2 轨迹模型

```python
@dataclass
class AuditEvent:
    run_id: str
    event_type: Literal[
        "run_start", "leader_plan", "worker_start", "worker_end",
        "tool_call", "approval_requested", "approval_decided",
        "run_end", "error"
    ]
    timestamp: datetime
    actor: str              # worker name 或 "leader" 或 "system"
    payload: dict           # 事件详情
    duration_ms: int | None = None
    tokens: int | None = None
```

领域层在每个节点执行时 emit `AuditEvent` → 落 `run_events`。UI 可按 run 回放整条轨迹。

### 7.3 Agent-as-a-Judge 钩子（预留）

预留评估器接口，读 `run_events` 产出评估结果。v1 只留接口，不实现。

```python
class Evaluator(Protocol):
    def evaluate(self, run_id: str) -> EvaluationResult: ...
```

## 8. 审批 / Human-in-the-loop

### 8.1 机制

- `ApprovalPolicy` 配在 Team/Worker 上，编译时转成 `interrupt()` 点
- 运行到 interrupt → run 状态置 `awaiting_approval` → 经 SSE 推送到 UI 审批待办
- 人类批准 → `Command(resume={"approved": True})` 续跑
- 拒绝 → Leader 重新规划或终止
- 审批记录落 `approvals` 表，可审计

### 8.2 审批粒度映射

| 粒度 | 触发时机 | 实现 |
|---|---|---|
| worker 级 | 该 Worker 每次执行前 | worker 节点前插 interrupt |
| tool 级 | 调用指定工具前 | ReAct 内工具调用前插 interrupt |
| step 级 | Leader 每步分配前 | leader_plan 后插 interrupt |

## 9. API（FastAPI + SSE 流式）

| 方法 | 端点 | 作用 |
|---|---|---|
| GET | `/api/teams` | 列出团队定义 |
| POST | `/api/teams` | 注册团队定义 |
| POST | `/api/runs` | 提交任务，启动一个 run |
| GET | `/api/runs/{id}/stream` | SSE 流式推送事件 |
| GET | `/api/runs/{id}/trace` | 查询完整执行轨迹 |
| POST | `/api/runs/{id}/approvals/{aid}` | 提交审批决策 |
| POST | `/api/runs/{id}/resume` | 恢复中断的 run |
| GET | `/api/dashboard` | 用量统计 |

### 9.1 SSE 事件类型

对齐 `AuditEvent.event_type`：`leader_plan` / `worker_start` / `worker_end` / `tool_call` / `approval_requested` / `approval_decided` / `run_end` / `error`。

## 10. Web UI 控制台

对标 AgentTeams「工作群 + 监控仪表盘」的本地版，五个页面：

1. **团队管理**：注册/编辑 Team（Leader + Workers + 工具 + 审批策略），YAML/表单双模式
2. **任务提交**：选团队 → 输入任务 → 启动 run
3. **实时看板**（核心）：agent 对话流（像工作群，看 Leader 和 Worker 之间在聊什么）+ 工具调用气泡 + 审批待办弹窗，支持人工干预
4. **轨迹回放**：选历史 run，按时间轴回放每一步、查看 token / 耗时
5. **用量仪表盘**：token 消耗、run 数、各 Worker 调用次数趋势

**技术选型**：纯 HTML + 原生 JS + 轻量 CSS（或 Alpine.js），FastAPI 托管静态资源，避免引入构建链。后期可换 React，但 v1 优先零构建。

## 11. 默认示例团队「研发小队」

为验证框架可用，内置一个开箱即用的团队：

| 角色 | 职责 | 工具 | 审批 |
|---|---|---|---|
| Leader（技术主管） | 拆需求、派活、汇总 | — | step 级（可选） |
| 需求分析员 | 拆用户故事、定验收标准 | `search_web` | — |
| 代码工程师 | 写/改代码 | `read_file`、`write_file`、`mcp:git` | `write_file` 前审批 |
| 测试员 | 写测试用例 | `read_file`、`write_file` | `write_file` 前审批 |
| Reviewer | 审查代码与测试 | `read_file` | — |

跑通它能验证：Leader-Worker 编排、ReAct 工具循环、MCP+原生工具混用、声明式审批、轨迹记录与回放、用量统计。

## 12. 项目结构

```
agentTeam/
├── pyproject.toml
├── README.md
├── agentteam/
│   ├── __init__.py
│   ├── domain/            # Team, Worker, Leader, ApprovalPolicy, Trace
│   │   ├── team.py
│   │   ├── worker.py
│   │   ├── approval.py
│   │   └── trace.py
│   ├── runtime/           # TeamCompiler, nodes, state, checkpointer
│   │   ├── graph.py
│   │   ├── nodes.py
│   │   ├── state.py
│   │   └── checkpointer.py
│   ├── tools/             # ToolRegistry, skills/, mcp.py
│   │   ├── registry.py
│   │   ├── skills/
│   │   └── mcp.py
│   ├── models/            # ModelProvider + adapters/
│   │   ├── provider.py
│   │   └── adapters/
│   ├── storage/           # db.py, runs.py, audit.py
│   │   ├── db.py
│   │   ├── runs.py
│   │   └── audit.py
│   ├── api/               # FastAPI server + routes/
│   │   ├── server.py
│   │   └── routes/
│   ├── web/               # 静态前端资源
│   └── cli.py             # CLI 入口
├── examples/
│   └── dev_team.py        # 研发小队示例
├── tests/                 # pytest 单元 + 集成
└── data/                  # agentteam.db
```

## 13. 错误处理

- **模型调用**：超时 + 指数退避重试（默认 3 次），失败抛出并记 `error` 事件
- **MCP 子进程**：崩溃自动重启，重启失败则该工具标记不可用并通知 Leader 换方案
- **Worker 执行失败**：Leader 可重试 / 换 Worker / 标记步骤失败后继续
- **审批超时**：可配超时策略（默认无限等待，可设为超时自动拒绝）
- **run 异常**：状态置 `failed`，轨迹保留供排查，可从 checkpoint 续跑

## 14. 测试策略（pytest）

- **单元测试**：领域模型（Team/Worker/ApprovalPolicy）、TeamCompiler 编译正确性、ToolRegistry（原生+MCP mock）、ModelProvider（mock 各适配器）
- **集成测试**：用 mock LLM（固定返回）跑完整 run——Leader 拆解→Worker 执行→审批中断→恢复→结束，验证状态流转与轨迹落库
- **审批流测试**：触发 interrupt → 模拟人类决策 → 续跑
- **API 测试**：FastAPI TestClient 验证端点 + SSE 事件序列

## 15. 技术依赖

| 依赖 | 用途 |
|---|---|
| `langgraph` | 执行内核（StateGraph、SqliteSaver、interrupt） |
| `langchain-core` | BaseChatModel、BaseTool、messages |
| `langchain-openai` | OpenAI / 兼容接口适配 |
| `dashscope` / `langchain-community` | Qwen（ChatTongyi）适配 |
| `langchain-anthropic` | Anthropic 适配 |
| `langchain-ollama` | Ollama 适配 |
| `mcp` | MCP Python SDK（load_mcp_tools） |
| `fastapi` + `uvicorn` | API 服务 |
| `sse-starlette` | SSE 流式推送 |
| `sqlalchemy` / 原生 sqlite3 | SQLite 存储 |
| `pydantic` | 数据校验 |
| `pytest` + `pytest-asyncio` | 测试 |

## 16. 里程碑（建议拆分）

1. **M1 基础设施**：项目骨架、ModelProvider、ToolRegistry（原生 Skill）、SQLite 存储
2. **M2 领域与编译**：Team/Worker/Leader/ApprovalPolicy 领域模型、TeamCompiler、StateGraph 编译、ReAct Worker 节点
3. **M3 审批与轨迹**：interrupt 审批、AuditEvent 轨迹落库、断点续跑
4. **M4 MCP 集成**：MCPServer 加载、工具注册、生命周期管理
5. **M5 API + Web UI**：FastAPI 端点、SSE、五页控制台
6. **M6 示例团队 + 测试**：研发小队、集成测试、文档
