# AgentTeam

> 本地多智能体协作框架，基于 Python + LangGraph 构建，遵循 pi-mono "提供原语而非成品" 的设计哲学。

核心只提供编排与编译的基础原语：递归 Agent 树、状态图编译、可插拔工具流水线、事件钩子、审批门注册表。产品级能力（PEP、审批策略、自进化、Webhook、RBAC）通过 stage / hook / gate 等扩展点由上层注入，core 不内置任何成品功能。

---

## 目录

- [核心特性](#核心特性)
- [架构总览](#架构总览)
- [核心原语](#核心原语)
- [快速开始](#快速开始)
- [配置](#配置)
- [API 参考](#api-参考)
- [CLI 命令](#cli-命令)
- [Skill 系统](#skill-系统)
- [预置团队](#预置团队)
- [Web 控制台](#web-控制台)
- [测试](#测试)
- [模块索引](#模块索引)
- [里程碑](#里程碑)

---

## 核心特性

### 编排能力

- **递归 Agent 树**：支持任意层级 supervisor / worker 嵌套，单 Worker 也能跑
- **两种执行模式**：sequential（顺序执行 plan）和 dag（依赖图并行执行，含条件分支与循环检测）
- **多模型供应商**：Qwen / OpenAI / Anthropic / Ollama 统一抽象，按 Agent 粒度绑定模型
- **MCP 集成**：基于 Model Context Protocol 加载外部工具，支持 stdio / http 传输
- **Agent 库**：`$ref` 引用复用，循环引用检测，深拷贝 + 字段覆盖
- **节点契约**：`PlanStep` 携带 `acceptance_criteria` / `budget_tokens` / `depends_on` / `condition`，`WorkerOutput` 区分 artifact / evidence / state_delta / failure 四类产出

### 核心原语（pi-mono 设计）

| 原语 | 文件 | 作用 |
|---|---|---|
| **Transcript 双层消息** | runtime/messages.py | 分离 transcript 消息与 LLM 消息，artifact/notification 不污染 LLM 上下文 |
| **ToolCall Pipeline** | runtime/tool_pipeline.py | 可插拔工具调用流水线，stage 串行执行，首个短路结果胜出 |
| **Hook Registry** | runtime/hooks.py | 全局事件钩子，handler 异常不中断主流程，支持 `pre_tool_call` / `post_tool_call` 等 |
| **Gate Registry** | runtime/gates.py | 数据驱动审批门注册表，`GateFactory` 协议解耦 gate 创建与编译器 |
| **Role Registry** | runtime/graph.py | role → `RoleSpec` 注册表，第三方可扩展新 role 无需改 TeamCompiler |

### 工程能力

- **SQLite 持久化**：WAL 模式 + 集中 conn_lock，幂等 schema 迁移框架（PRAGMA user_version）
- **SSE 实时推送**：run 执行轨迹实时流，断线重连支持
- **Checkpoint 续跑**：interrupted run 服务重启后 lazy recompile + 从 checkpoint 续跑
- **配置集中化**：pydantic-settings 统一管理所有 `AGENTTEAM_*` 环境变量
- **结构化日志**：text / json 双格式，`get_logger(name)` 自动初始化

---

## 架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                         Web 控制台 (React)                        │
│           Dashboard · Teams · Runs · Skills · RunDetail          │
└──────────────────────────────┬──────────────────────────────────┘
                               │ HTTP / SSE
┌──────────────────────────────┴──────────────────────────────────┐
│                         FastAPI (api/)                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │  teams   │ │  runs    │ │ library  │ │  skills  │ │dashboard│ │
│  │  CRUD    │ │ SSE+取消 │ │ Agent库  │ │  查询    │ │ 统计聚合│ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └────────┘ │
│              ┌──────────────────────────────────┐                │
│              │     run_manager                  │                │
│              │ 后台线程 + interrupt/resume       │                │
│              └──────────────────────────────────┘                │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────┴──────────────────────────────────┐
│                       Runtime (runtime/)                          │
│   TeamCompiler ─► StateGraph ─► leader_plan → worker ReAct        │
│                  → leader_review                                  │
│                                                                  │
│   核心原语(可插拔扩展点):                                          │
│   • ToolCallPipeline  (tool_pipeline.py)                         │
│   • HookRegistry      (hooks.py)                                 │
│   • GateRegistry      (gates.py)                                 │
│   • RoleRegistry      (graph.py)                                 │
│   • TranscriptMessage (messages.py)                              │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────┴──────────────────────────────────┐
│                       Domain (domain/)                            │
│   Team · Agent · Worker · Leader · ApprovalPolicy · MCPServer    │
│   AgentLibrary（$ref 引用复用 + 循环检测）                          │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────┴──────────────────────────────────┐
│                      Storage (storage/)                           │
│   SQLite + WAL + 集中 conn_lock + 幂等迁移框架                    │
│   runs · run_events · approvals · teams · library_agents          │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────┴──────────────────────────────────┐
│                      Models (models/)                            │
│   Qwen · OpenAI · Anthropic · Ollama 统一 ModelProvider 抽象       │
└──────────────────────────────────────────────────────────────────┘
```

---

## 核心原语

### Transcript 双层消息

借鉴 pi-mono AgentMessage，分离 transcript 消息与 LLM 消息。会话历史可包含 artifact / notification / compaction-summary 等自定义类型，参与持久化与 UI 渲染，但不污染 LLM 上下文。

```python
from agentteam.runtime.messages import (
    ArtifactMessage, NotificationMessage, CompactionSummary,
    TranscriptMessage, convert_to_llm, transform_context,
)

transcript: list[TranscriptMessage] = [
    SystemMessage(content="..."),
    HumanMessage(content="..."),
    ArtifactMessage(worker="coder", artifact="def hello(): ..."),  # 不进 LLM
    NotificationMessage(message="审批请求已发出"),                  # 不进 LLM
]

# 调 LLM 前过滤+转换
llm_messages = convert_to_llm(transcript)  # 仅 SystemMessage/HumanMessage + 摘要

# 上下文窗口管理(必须不抛错)
trimmed = transform_context(transcript, max_messages=20)
```

### ToolCall Pipeline

把 `make_tool_step` 内嵌的"拦截 / 审批 / 执行"解耦为可插拔 stage。core 仅提供 pipeline 框架与默认 `ExecutionStage`，成品功能通过 stage 或 hook 注入。

```python
from agentteam.runtime.tool_pipeline import (
    ToolCallPipeline, ToolCallContext, ToolCallStage, ExecutionStage,
    build_default_pipeline,
)

# 自定义 stage(如 PEP 拦截、审批、限流)
class PEPStage:
    def process(self, ctx: ToolCallContext):
        if not self._check(ctx):
            return ToolCallResult(
                tool_call_id=ctx.tool_call["id"],
                content="PEP 拒绝",
                is_error=True,
            )
        return None  # 放行下一 stage

pipeline = ToolCallPipeline([PEPStage(), ExecutionStage(tool_map)])
# 或用默认 pipeline(仅 ExecutionStage)
pipeline = build_default_pipeline(tool_map)
```

`make_tool_step` 在每个 tool_call 前后 emit `pre_tool_call` / `post_tool_call` 钩子，handler 可改写 ctx.metadata 影响后续 stage。

### Hook Registry

全局事件钩子机制，替代散落各处的硬编码触发点。handler 异常不中断主流程（记日志后继续），回调按注册顺序串行执行。

```python
from agentteam.runtime.hooks import get_hooks

hooks = get_hooks()

@hooks.on("pre_tool_call")
def log_tool_call(ctx):
    print(f"agent={ctx['agent_name']} tool={ctx['tool_call']['name']}")
    # 返回 dict 会合并进 ctx
    return {"metadata": {"logged": True}}
```

### Gate Registry

数据驱动审批门注册表，借鉴 RoleRegistry。把 `step_gate` / `worker_gate` 从 `TeamCompiler._compile_supervisor` 中解耦，core 不内置任何 gate，gate 作为扩展由上层注册。

```python
from agentteam.runtime.gates import get_gates, GateNode, GateFactory

class MyGateFactory(GateFactory):
    def create(self, agent, child_targets, compiler_deps=None):
        if not getattr(agent, "approval_policy", None):
            return None  # 该 agent 不需要 gate
        return GateNode(
            name=f"{agent.name}_gate",
            node_fn=self._build_node(agent),
            route_after=lambda state: "END" if _rejected(state) else "...",
        )

get_gates().register("step", MyGateFactory())
```

### Role Registry

role → `RoleSpec` 注册表（class-level 单例），第三方可扩展新 role（如 `reviewer` / `validator`）无需改 TeamCompiler 源码。

---

## 快速开始

### 安装

```bash
pip install -e ".[qwen,dev]"
```

可选 extras：`qwen` / `openai` / `anthropic` / `ollama` / `dev`（pytest + ruff）

### 启动服务

```bash
uvicorn agentteam.api.server:create_app --factory
```

启动后：
- API 服务：http://localhost:8000
- Web 控制台：http://localhost:8000（浏览器打开）
- API 文档：http://localhost:8000/docs

### 注册团队

```bash
# 注册预置团队
agentteam register-team agentteam/presets/enterprise_dev.py

# 注册自定义 Team 配置文件
agentteam register-team path/to/team.py
```

### 提交任务

```bash
curl -X POST http://localhost:8000/api/runs \
  -H "Content-Type: application/json" \
  -d '{"team_name": "enterprise_dev", "task": "实现一个 hello world 程序"}'
```

### 查看实时轨迹

```bash
# SSE 实时事件流
curl -N http://localhost:8000/api/runs/{run_id}/stream
```

或浏览器打开 http://localhost:8000 进入 Web 控制台查看。

---

## 配置

所有配置通过环境变量 `AGENTTEAM_*` 注入，零配置可启动。

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `AGENTTEAM_DB_PATH` | `data/agentteam.db` | SQLite 数据库路径 |
| `AGENTTEAM_LOG_LEVEL` | `WARNING` | 日志级别（DEBUG/INFO/WARNING/ERROR） |
| `AGENTTEAM_LOG_FORMAT` | `text` | 日志格式：`text` 或 `json` |
| `AGENTTEAM_EVENT_QUEUE_SIZE` | `1000` | EventBus 每订阅者队列上限 |
| `AGENTTEAM_MAX_RUN_WORKERS` | `32` | run 线程池大小 |
| `AGENTTEAM_MAX_EVOLUTION_WORKERS` | `4` | evolution 线程池大小 |
| `AGENTTEAM_INTERRUPTED_TTL_SECONDS` | `21600` | interrupted run 内存态 TTL（6h，0 禁用） |
| `AGENTTEAM_INTERRUPTED_SWEEP_INTERVAL_SECONDS` | `600` | interrupted run 清理任务间隔（秒） |
| `AGENTTEAM_AUTH_ENABLED` | `false` | 启用 API Key 鉴权 |
| `AGENTTEAM_AUTH_API_KEYS` | `""` | 合法 API Key 列表，逗号分隔 |
| `AGENTTEAM_SECRET_KEY` | `""` | 凭证加密主密钥（32 字节 hex/base64，空退化为明文） |

Python 代码内访问配置：

```python
from agentteam.config import get_settings

settings = get_settings()
print(settings.max_run_workers)  # int,已校验
```

测试中临时覆盖：

```python
from agentteam.config import override_settings

with override_settings(max_run_workers=2) as s:
    assert s.max_run_workers == 2
# 退出后恢复原值
```

---

## API 参考

### Teams 团队管理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/teams` | 列出所有团队 |
| POST | `/api/teams` | 注册新团队 |
| GET | `/api/teams/{name}` | 查看团队详情 |
| PUT | `/api/teams/{name}` | 更新团队配置 |
| DELETE | `/api/teams/{name}` | 删除团队 |

### Runs 任务执行

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/runs` | 提交任务（自动启动） |
| GET | `/api/runs` | 列出任务（支持 status / team_name 过滤） |
| GET | `/api/runs/{run_id}` | 查看任务详情 |
| GET | `/api/runs/{run_id}/trace` | 查看执行轨迹（支持 `chain=call\|tool\|decision` 过滤） |
| GET | `/api/runs/{run_id}/stream` | SSE 实时事件流 |
| POST | `/api/runs/{run_id}/cancel` | 取消正在执行的 run |
| GET | `/api/runs/{run_id}/approvals` | 列出 run 的所有审批节点 |

### Library Agent 库

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/library/agents` | 列出库中所有 Agent |
| POST | `/api/library/agents` | 注册 Agent 到库 |
| GET | `/api/library/agents/{name}` | 查看 Agent 详情 |
| PUT | `/api/library/agents/{name}` | 更新 Agent |
| DELETE | `/api/library/agents/{name}` | 删除 Agent |

### Skills Skill 系统

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/skills/` | 列出所有可用 skill |
| GET | `/api/skills/{name}` | 查看指定 skill 内容 |

### Dashboard 仪表盘

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/dashboard` | 基础统计（total_runs/total_tokens/by_status/by_team/recent_runs） |
| GET | `/api/dashboard/multi_dim` | 多维统计（含 by_chain/top_tools/tokens_by_team） |

---

## CLI 命令

```bash
# 注册内置研发小队到 API 服务
agentteam register-dev-team [--api URL]

# 注册任意 Team 配置文件
agentteam register-team FILE [--api URL]

# 列出已注册团队
agentteam list-teams [--api URL]

# 注册专家库
agentteam register-library FILE [--api URL]
```

默认 API URL 为 `http://localhost:8000`。

---

## Skill 系统

Skill 是注入到 Agent system_prompt 的领域知识片段，与 Tool 不同：Tool 是可执行函数，Skill 是文本指引。

### 预置 Skill

存放于 `skills/` 目录：

- `code_review.md` — 代码审查指引
- `error_handling.md` — 错误处理规范
- `testing_strategy.md` — 测试策略

### 使用方式

在 Agent 定义中通过 `skills` 字段引用：

```python
from agentteam.domain.agent import Agent

coder = Agent(
    name="coder",
    role="worker",
    system_prompt="You are a coder.",
    skills=["code_review", "testing_strategy"],  # 引用 skill 名
)
```

编译期 `SkillLoader.load(agent.skills)` 加载内容，注入到 `react_messages[1]` 位置（system_prompt 之后、task 之前），格式：

```
<skill name="code_review">审查代码 skill 内容</skill>
<skill name="testing_strategy">测试策略 skill 内容</skill>
```

---

## 预置团队

`agentteam/presets/` 提供 7 个开箱即用的团队模板：

| Preset | 文件 | 适用场景 |
|---|---|---|
| `enterprise_dev` | `enterprise_dev.py` | 企业研发团队（leader + coder + tester + reviewer） |
| `customer_support` | `customer_support.py` | 客户支持团队（triage + resolver + escalator） |
| `data_analysis` | `data_analysis.py` | 数据分析团队（query + visualize + report） |
| `content_marketing` | `content_marketing.py` | 内容营销团队（researcher + writer + editor） |
| `schedule_management` | `schedule_management.py` | 日程管理团队（planner + reminder + prioritizer，挂接 calendar MCP） |
| `project_management` | `project_management.py` | 项目管理团队 |
| `personal_assistant` | `personal_assistant.py` | 个人助理团队 |

通过 CLI 一键安装：

```bash
agentteam register-team agentteam/presets/enterprise_dev.py
```

或编程方式安装：

```python
from agentteam.presets import list_presets, install_preset_to_api

print(list_presets())  # ['enterprise_dev', 'customer_support', ...]
install_preset_to_api("enterprise_dev", api_url="http://localhost:8000")
```

---

## Web 控制台

`web/` 目录提供 React + antd + Vite 构建的控制台：

- **Dashboard** — 用量统计、趋势图、最近 run 列表
- **Teams** — 团队 CRUD、配置编辑
- **Runs** — run 列表、状态过滤
- **RunDetail** — run 详情、SSE 实时轨迹
- **Skills** — Skill 列表、内容查看

### 开发

```bash
cd web
npm install
npm run dev    # 开发模式(http://localhost:5173)
npm run build  # 生产构建(产物到 web/dist/,API 服务自动挂载)
```

构建产物 `web/dist/` 会被 API 服务自动挂载到根路径，生产部署只需启动 `uvicorn` 即可。

---

## 测试

### 运行测试

```bash
# 全量测试
python -m pytest tests/ -q

# 仅 API 测试
python -m pytest tests/api/ -v

# 仅 runtime 测试
python -m pytest tests/runtime/ -v

# 仅集成测试
python -m pytest tests/integration/ -v
```

### 测试组织

| 目录 | 覆盖范围 |
|---|---|
| `tests/api/` | API 路由、SSE、cancel、并发、序列化 |
| `tests/domain/` | Team/Agent/Worker/Library 领域模型 |
| `tests/runtime/` | TeamCompiler、nodes、ToolCallPipeline、plan dag、role registry |
| `tests/storage/` | 所有 Repo + 迁移框架 |
| `tests/integration/` | 端到端：多级团队、MCP 集成、preset 安装 |
| `tests/models/` | ModelProvider + 各 adapter |
| `tests/tools/` | ToolRegistry + 内置 skill + MCP 工具 |
| `tests/presets/` | 预置团队 catalog + 安装 |

### 测试规模

- **569 测试用例 / 77 测试文件**，覆盖所有核心模块
- 含端到端集成测试（多级团队 + MCP 全流程）
- 含并发安全测试（library check-then-set、SSE 断线重连）

---

## 模块索引

```
agentteam/
├── api/                      # FastAPI 后端
│   ├── server.py             # app 工厂 create_app
│   ├── run_manager.py        # 后台线程执行 + interrupt/resume
│   ├── store.py              # TeamStore 内存注册表
│   ├── events.py             # EventBus + BroadcastTraceWriter
│   └── routes/
│       ├── teams.py          # 团队 CRUD
│       ├── runs.py           # 任务执行 + SSE + cancel
│       ├── library.py        # Agent 库 CRUD
│       ├── skills.py         # Skill 查询
│       └── dashboard.py      # 仪表盘
├── domain/                   # 领域模型
│   ├── team.py               # Team 容器(支持 root 树 / legacy leader+workers)
│   ├── agent.py              # Agent 节点(支持 ref / children / skills)
│   ├── worker.py             # Worker 兼容层
│   ├── library.py            # AgentLibrary($ref 复用 + 循环检测)
│   ├── approval.py           # ApprovalPolicy(step/worker/tool 三级,声明式)
│   ├── mcp_server.py         # MCPServer
│   └── serializer.py         # Team ↔ JSON 双向转换
├── runtime/                  # 执行内核
│   ├── graph.py              # TeamCompiler + RoleRegistry + DAG 条件求值
│   ├── nodes.py              # leader_plan / worker ReAct / leader_review
│   ├── state.py              # TeamState / WorkerState schema
│   ├── messages.py           # Transcript 双层消息模型(核心原语)
│   ├── tool_pipeline.py      # ToolCallPipeline 可插拔工具流水线(核心原语)
│   ├── hooks.py              # HookRegistry 事件钩子(核心原语)
│   ├── gates.py              # GateRegistry 审批门注册表(核心原语)
│   ├── skills.py             # SkillLoader(扫描 .md + 缓存 + reload)
│   ├── trace.py              # TraceWriter(三链 + per-run trace_id)
│   └── errors.py             # RunCancelledError 等
├── storage/                  # SQLite 持久化
│   ├── db.py                 # init_db + 迁移框架(幂等)
│   ├── base.py               # BaseSqliteRepo(共享 conn + lock)
│   ├── runs.py               # runs 表 + sum_tokens_by_team
│   ├── audit.py              # run_events(三链 + 聚合)
│   ├── teams.py              # teams 表
│   └── library.py            # library_agents 表
├── models/                   # 模型供应商抽象
│   ├── provider.py           # ModelProvider + ModelRef
│   └── adapters/             # qwen / openai / anthropic / ollama
├── tools/                    # 工具系统
│   ├── registry.py           # ToolRegistry(带缓存)
│   ├── mcp.py                # MCP 工具加载
│   └── skills/               # 内置 skill(read_file / search_web / ...)
├── presets/                  # 预置团队模板(7 个)
├── config.py                 # pydantic-settings 集中配置
├── logging_config.py         # 集中式 logging(text/json 双格式)
├── plugins.py                # 插件自动发现(entry_points)
└── cli.py                    # CLI 入口
```

---

## 里程碑

### 基础能力

- [x] **M1** 基础设施层（SQLite + 迁移框架 + 配置 + 日志）
- [x] **M2** 领域与编译（Team / Worker / TeamCompiler / LangGraph）
- [x] **M3** 审批与轨迹（step / worker / tool 三级审批 + TraceWriter）
- [x] **M4** MCP 集成（子图 ReAct + 工具级审批 + MCP 工具加载）
- [x] **M5a** API（FastAPI + SSE + RunManager + interrupt/resume）
- [x] **M5b** Web UI（React + antd + SSE 实时控制台）
- [x] **M6** 示例团队 + 集成测试
- [x] **M7** DAG 执行模式（依赖图并行 + 条件分支 + 循环检测）

### 架构调整（借鉴 pi-mono 设计哲学）

- [x] **A1** Transcript 双层消息模型（`runtime/messages.py`）
- [x] **A2** ToolCall Pipeline 可插拔流水线（`runtime/tool_pipeline.py`）
- [x] **A3** Hook Registry 统一事件钩子（`runtime/hooks.py`）
- [x] **A4** Gate Registry 数据驱动审批门（`runtime/gates.py`）
- [x] **A5** Core / Product 分层 — 删除成品层（PEP / 审批 / 自进化 / Webhook / RBAC），core 仅保留原语与扩展点

---

## License

MIT
