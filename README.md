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
- [容器部署（内网一键起）](#容器部署内网一键起)

---

## 核心特性

### 编排能力

- **递归 Agent 树**：支持任意层级 supervisor / worker 嵌套，单 Worker 也能跑
- **两种执行模式**：sequential（顺序执行 plan）和 dag（依赖图并行执行，含条件分支与循环检测）
- **多模型供应商**：Qwen / OpenAI / Anthropic / Ollama 统一抽象，按 Agent 粒度绑定模型
- **MCP 集成**：基于 Model Context Protocol 加载外部工具，支持 stdio / http 传输
- **Agent 库**：`$ref` 引用复用，循环引用检测，深拷贝 + 字段覆盖
- **节点契约**：`PlanStep` 携带 `acceptance_criteria` / `budget_tokens` / `depends_on` / `condition`，`WorkerOutput` 区分 artifact / evidence / state_delta / failure 四类产出
- **通信纪律与授权（SP12，借鉴 dsh-superagent）**：leader/worker 职责硬隔离 + 通知预算、IM 通知 Outbox 可靠出站、事件驱动 Trigger（Fast/Slow Path 两级过滤）、Mandate 授权凭据（授权与执行分离）、追问直通绿色通道（人对 worker 单向直达，不过 leader）

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

- `agentteam.models` —— 多供应商模型抽象（Qwen/OpenAI/Anthropic/Ollama）
- `agentteam.tools` —— ToolRegistry + 原生技能（read_file/write_file/list_dir）+ MCP 工具加载
- `agentteam.storage` —— SQLite 持久化（runs / run_events / approvals）
- `agentteam.domain` —— 领域模型（Team/Worker/Leader/ApprovalPolicy/MCPServer）
- `agentteam.runtime` —— 执行内核（TeamCompiler + LangGraph StateGraph 编译执行）
  - `state.py` — TeamState / WorkerState 状态 schema
  - `nodes.py` — leader_plan / worker ReAct 子图 / leader_review 节点工厂
  - `graph.py` — TeamCompiler（Team → StateGraph 编译，含审批门 + MCP 加载）
  - `trace.py` — TraceWriter 协议（SQLite / Fake 实现）
  - `approval.py` — 审批门节点（step 级 / worker 级 / tool 级，interrupt 实现）
- `agentteam.api` —— FastAPI 后端 API（团队注册、任务提交、SSE 实时推送、审批续跑、用量统计）
  - `server.py` — FastAPI app 工厂（create_app）
  - `serializer.py` — Team JSON ↔ dataclass 转换
  - `store.py` — TeamStore 内存注册表
  - `events.py` — EventBus + BroadcastTraceWriter
  - `run_manager.py` — 后台线程执行 + interrupt/resume
  - `routes/` — teams / runs / dashboard 路由
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

三种启动方式：**A. 本机开发模式**（最快跑通）、**B. Docker 一键起**（内网部署）、**C. 测试脚本**。

执行引擎是 **langgraph**：纯 Python 进程内执行，唯一前置是**任意一家模型的 API Key
环境变量**（`DASHSCOPE_API_KEY`（Qwen）/ `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`，
或本地 Ollama 免 Key）。编排/三级审批/自进化/Web 控制台全部可用，
tool 级审批是「事前拦截」语义。

### 方式 A：本机开发模式

```bash
# 0. 安装（首次执行一次；extras 可选 qwen/openai/anthropic/ollama/dev）
pip install -e ".[qwen,dev]"

# 1. 启动控制平面：API + Web 控制台（同源托管）
AGENTTEAM_SKILLS_DIR=./skills uvicorn agentteam.api.server:create_app --factory --port 8000
#   - API 服务 / Web 控制台：http://localhost:8000
#   - API 文档：http://localhost:8000/docs
#   - AGENTTEAM_SKILLS_DIR 让 Web 控制台的 Skills 页有数据（默认空）

# 2. 注册团队（三选一；前提是你已在第 1 步把服务跑起来）
python -m agentteam.cli install-preset enterprise_dev     # 企业预设（三级团队）
python -m agentteam.cli register-dev-team                 # 研发小队
#   ↑ 先配任一家模型 Key，如 export DASHSCOPE_API_KEY=sk-xxx（Qwen）
#   （OPENAI_API_KEY / ANTHROPIC_API_KEY / 本地 Ollama 亦可，见「配置」与模型适配器）
python -m agentteam.cli register-team path/to/team.py     # 自定义 Team 配置文件

# 3. 提交任务
curl -X POST http://localhost:8000/api/runs   -H "Content-Type: application/json"   -d '{"team_name": "enterprise_dev", "task": "实现一个 hello world 程序"}'

# 4. 查看实时轨迹
curl -N http://localhost:8000/api/runs/{run_id}/stream   # SSE 事件流
#   或浏览器打开 http://localhost:8000 进 Web 控制台（Runs → RunDetail 页可审批/取消）
```

要点：

- langgraph 引擎的 run 需要对应供应商的 API Key（如 `DASHSCOPE_API_KEY`）。
- 首跑提交后即可在 Web 控制台看到实时轨迹；**审批触发时 run 变 `interrupted`**，
  在 Runs → RunDetail 页点批准/拒绝即续跑（服务重启后仍可续）。

### 常见启动问题

| 现象 | 原因与处理 |
|---|---|
| Web 控制台 Skills 页为空 / `GET /api/skills/{name}` 404 | 未设技能目录：启动 API 时带 `AGENTTEAM_SKILLS_DIR=./skills`（Docker 镜像已内置） |
| run failed，error 里含 429 / FreeUsageLimitError | 免费共享模型限流：等额度恢复，或换自有 Key（`DEEPSEEK_API_KEY` 等放 `.env`） |
| run 卡在 `interrupted` | 正常——等待人工审批；到 Web 控制台 Runs → RunDetail 点批准/拒绝 |
| Web 控制台 404 / 无样式 | 未构建前端：`cd web && npm install && npm run build`（dist 会由 API 同源托管） |

### 方式 B：Docker 一键起（内网部署）

```bash
# 一键起容器（控制平面，数据落 agentteam-data 卷）
docker compose up -d --build

# 访问 http://127.0.0.1:8000（只绑 loopback，对外走内网反代/SSH 隧道）
```

常用变体与卷挂载见 [容器部署](#容器部署内网一键起)。

### 方式 C：测试脚本

```bash
python -m pytest tests -q                                 # 全量

cd web && npm install && npm run build && npx vitest run  # 前端构建 + 前端测试
```

平台配置（DB/日志/鉴权/配额）见 [配置](#配置)。

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
| `AGENTTEAM_NOTIFY_WEBHOOK_URL` | `""` | 全局默认 IM webhook（飞书/企微/钉钉 bot）；Team 的 `webhook_url` 字段优先于它 |
| `AGENTTEAM_NOTIFY_PROVIDER` | `generic` | 通知消息格式：`feishu` / `wecom` / `dingtalk` / `generic` |
| `AGENTTEAM_CONSOLE_URL` | `http://127.0.0.1:8040` | 控制台外部可达地址（通知里的跳转链接基准） |
| `AGENTTEAM_NOTIFY_TIMEOUT_SECONDS` | `5.0` | webhook POST 超时秒数 |
| `AGENTTEAM_NOTIFY_RETRY_MAX` | `5` | 单条通知连续失败多少次后置 dropped（回执保留 last_error 可查） |
| `AGENTTEAM_NOTIFY_BACKOFF_BASE` | `5.0` | 通知重试退避起点秒数（指数翻倍，上限 300s） |
| `AGENTTEAM_MEMORY_ENABLED` | `true` | 跨 run 记忆层：run 终态后蒸馏团队记忆，并在后续 run 注入 leader 上下文 |
| `AGENTTEAM_MEMORY_MAX_ENTRIES` | `8` | 每次注入 leader 上下文的最近记忆条数上限 |
| `AGENTTEAM_MEMORY_INJECT_BUDGET_TOKENS` | `600` | 记忆注入的 token 预算（chars/4 估算）；`0` 禁用注入（蒸馏仍进行） |
| `AGENTTEAM_TRIGGERS_SLOW_PATH_ENABLED` | `true` | 事件驱动 Trigger 允许 Slow Path（LLM 语义判定）；`false` 只走 Fast Path 确定性条件 |
| `AGENTTEAM_SKILLS_DIR` | 不设＝不加载 | SP7 技能目录（`uvicorn --factory` 无法传参时的唯一入口） |

### IM 通知（等待审批 / 完成 / 失败推送）

run 生命周期关键事件会推送到 IM webhook——审批等待（interrupted）、正常完成、失败。
两种配置方式：

```jsonc
// 方式一：全局（所有团队生效）—— 启动前设置环境变量
AGENTTEAM_NOTIFY_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
AGENTTEAM_NOTIFY_PROVIDER=feishu        // 消息体按飞书 bot 格式组装

// 方式二：团队级 —— 注册 Team 时带 webhook_url（优先级高于全局）
{"name": "dev_lead", ..., "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"}
```

`notify_provider` 决定请求体格式（飞书 `msg_type:text`、企微/钉钉 `msgtype:markdown`、
`generic` 输出自解释 JSON 可对接自建接收端/Matrix 桥）。通知失败只记日志，
绝不影响 run 主流程。取消（cancelled）也会通知——人工停止 = 撤权（见下文
Mandate），属于授权事件回执而非普通打扰。

**可靠出站（SP12b outbox）**：通知先落库（`notify_outbox` 表）再由后台 worker
发送——进程重启后未发通知自动续发；失败按 5s 起步指数退避（上限 300s），
连续 `RETRY_MAX` 次失败置 dropped（不丢审计信息）；同一 run 的多条通知按
入队序串行发送（「等待审批」必先于结果到达）。发送回执可查：
`GET /api/runs/{id}/notifications`（status/attempts/last_error/时间）。
正文超过 300 字符或 8 行自动截断（每条通知都是团队的注意力成本）。

### 模型分层指南（SP12a：领导轻量、工人强力）

借鉴 DeepSeek Super-agent 的 Router/Worker 分层：**leader 是协调者不是执行者**
（分派纪律写死在 prompt：指令只含目标+验收标准、≤30 词、不写实现方案），
因此 leader 用便宜快的小模型即可；worker 干重活，配最强模型：

- 直接给 leader/worker 配不同 `model`；
- worker 若必须向人提问，回复末尾用单独一行 `QUESTION: <问题>`（一次最多一个），
  轨迹中表现为 `worker_attention` 事件。

### 事件驱动 Trigger（SP12c：入站 webhook + 两级过滤）

对标 DeepSeek Super-agent 的事件驱动设计（第 3 节 Trigger）：Agent 不再只能等人
`POST /api/runs`，任何能发 JSON 的系统（GitHub webhook、IM bot、cron、自建巡检）
都可以成为事件源。规则存 SQLite（`trigger_rules` 表），命中后走与人工提交
**完全相同**的 run 流程（配额/审计），`triggered_by_user` 记
`trigger:<规则名>` 以区分人工触发。

**两级过滤**（Fast/Slow Path，规则里二选一或组合）：

- **Fast Path（确定性条件）**：`conditions` 全部命中才行动，程序求值零模型成本。
  算子 `eq/ne/contains/in/exists/regex`，字段是事件的点路径（`payload.title`、
  `headers.x-github-event`…）。
- **Slow Path（LLM 语义判定）**：开了 `slow_path` 的规则，conditions 退化为
  「候选预过滤」，候选事件交给 LLM 按 `slow_path.prompt` 的自然语言策略判定
  是否行动（拿不准倾向不行动）。无模型 Key / 全局关闭 / 判定异常时静默跳过。

```bash
# 1. 注册规则:GitHub push 事件 → 自动派活
curl -X POST http://localhost:8000/api/triggers -H "Content-Type: application/json" -d '{
  "name": "gh_push", "team_name": "dev_lead", "secret": "s3cret",
  "conditions": [{"field": "headers.x-github-event", "op": "eq", "value": "push"}],
  "task_template": "仓库有新 push（{payload.head_commit.message}），请 review 该次提交并修复发现的问题"
}'

# 2. 外部系统 POST 事件(命中即自动创建 run)
curl -X POST http://localhost:8000/api/triggers/webhook/gh_push \
  -H "Content-Type: application/json" -H "X-Trigger-Secret: s3cret" \
  -d '{"head_commit": {"message": "fix: 登录崩溃"}}'
# → {"matched": true, "run_id": "...", "via": "fast_path"}

# 3. Slow Path 规则:群里讨论 bug → LLM 判定是否值得派活(静默复现式策略)
curl -X POST http://localhost:8000/api/triggers -H "Content-Type: application/json" -d '{
  "name": "im_triage", "team_name": "dev_lead",
  "slow_path": {"enabled": true, "prompt": "只在消息明确报告了可复现缺陷且无人认领时行动"},
  "task_template": "群里反馈：{payload.text}。请先复现，确认后修复并汇报"
}'
```

规则管理：`GET /api/triggers`（secret 打码）/ `GET|PUT|DELETE /api/triggers/{name}`。
命中但提交失败（配额 429 / 编译失败 400）会透传对应状态码；任务文本从事件字段
提取时单值截断 2000 字符、总长 4000 字符，防外部事件撑爆 prompt。

### Mandate 授权模型（SP12：授权与执行分离）

借鉴 dsh-superagent 的 Mandate 设计：每个 run 携带一份**授权凭据**（谁、依据
什么授权规则版本、允许做什么），向下只读继承——worker 不放大范围（越界 =
failure，由 leader_review 判定）。

- `basis`：`explicit`（人经 API 提交）| `inferred`（事件 Trigger 命中规则自动触发）；
- `submitted_by`：提交者身份（API 用户名 / `trigger:<规则名>` / 缺省 `api`）；
- `policy_version`：授权规则版本 = 团队配置内容哈希。团队配置热更新 → 版本变更 →
  进行中 run 记 `policy_expired` 注记（**不中断运行**，对齐 dsh「不撤销在途
  Mandate、变更通知到在途工单」）；
- **审批门 = 授权管理**：工具级审批通过 = 该操作授权延续，拒绝 = 该 step 授权
  终止；审批历史记录决策当时的 `mandate_version`；
- **人的停止 = 撤权**：cancel run 视为授权终止，通知带「人工撤权」文案，
  事件带 `revoked_by_human` 标记。

凭据存 `runs.mandate_json`（迁移 v16），`GET /api/runs/{run_id}` 返回 `mandate`
对象；旧 run 无凭据按无授权兼容。

### 绿色通道（SP12d：追问直通 worker）

对标 dsh-superagent 的 Subscription fan-out：**人对运行中/等待中 worker 的
单向追问直达 worker 会话，不过 leader**（最大单项注意力节省）。通信拓扑红线
不破——消息**只入不回**，worker 的回答仍走正常汇报链（→ leader → 出口）：

```bash
# 追问指定 worker（必须显式指定，禁止广播）
curl -X POST http://localhost:8000/api/runs/<run_id>/messages -H "Content-Type: application/json" -d '{
  "target": "worker", "worker_name": "coder", "text": "登录接口的 token 有效期改回 7 天"
}'

# 提醒 leader（只产生事件，leader 可据此 stop，不可代答/重新派工）
curl -X POST http://localhost:8000/api/runs/<run_id>/messages -H "Content-Type: application/json" -d '{
  "target": "leader", "text": "预算只剩一半了，收尾时优先跑测试"
}'
```

投递语义：run 必须处于 running/interrupted（终态返回 409，提示重新提交而非
复活）；langgraph 在 worker 每次 LLM 调用前注入
`[人工追问]` 消息（与取消检查同轮询）。投递产生 `follow_up_delivered` 轨迹事件
（含队列深度），前端无监听时零影响。

### 跨 run 记忆层（团队经验蒸馏与注入）

run 终态（completed / failed）后，`MemoryEngine` 在后台线程用 LLM 把本次执行
trace 蒸馏成一条团队记忆（结果要点 / 踩坑 / 涉及文件），存入 SQLite `run_memories`
表；下一次同团队 run 启动时，最近 N 条记忆按 token 预算裁剪后注入 leader 的
plan 上下文（`<memory>` 块），团队因此「记得」上次做过什么、踩过什么坑。

```jsonc
// 全部可关：
AGENTTEAM_MEMORY_ENABLED=0                    // 关闭蒸馏 + 注入
AGENTTEAM_MEMORY_INJECT_BUDGET_TOKENS=0       // 只关注入（仍蒸馏，供 Teams 页查看）
```

记忆查看与清理：Web 控制台 Teams 页每张卡片的「Team Memory」折叠面板，或 API：

```bash
curl http://localhost:8000/api/teams/dev_lead/memories       # 列表（新→旧）
curl -X DELETE http://localhost:8000/api/teams/dev_lead/memories/3   # 删除（记忆卫生）
```

蒸馏失败只记日志，绝不影响 run 主流程；记忆按 team 隔离，跨队不可见。

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
| GET | `/api/teams/{name}/memories` | 团队跨 run 记忆列表（记忆层） |
| DELETE | `/api/teams/{name}/memories/{memory_id}` | 删除一条团队记忆 |

### Runs 任务执行

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/runs` | 提交任务（自动启动） |
| GET | `/api/runs` | 列出任务（支持 status / team_name 过滤） |
| GET | `/api/runs/{run_id}` | 查看任务详情 |
| GET | `/api/runs/{run_id}/trace` | 查看执行轨迹（支持 `chain=call\|tool\|decision` 过滤） |
| GET | `/api/runs/{run_id}/stream` | SSE 实时事件流 |
| POST | `/api/runs/{run_id}/cancel` | 取消正在执行的 run（= 人工撤权，见 Mandate） |
| POST | `/api/runs/{run_id}/messages` | **追问直通**（SP12d）：人对运行中/等待中的指定 worker 或 leader 投递追问 |
| GET | `/api/runs/{run_id}/approvals` | 列出 run 的所有审批节点（含决策时的 `mandate_version`） |
| GET | `/api/runs/{run_id}/pending-approval` | 待审批预览（工具级审批返回 write_file diff / bash 命令等） |

### Triggers 事件驱动（SP12c）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/triggers` | 注册触发规则（name 唯一；条件/任务模板/Slow Path 策略） |
| GET | `/api/triggers` | 列出规则（可 `?team_name=` 过滤；secret 打码） |
| GET | `/api/triggers/{name}` | 查看规则详情 |
| PUT | `/api/triggers/{name}` | 更新规则 |
| DELETE | `/api/triggers/{name}` | 删除规则 |
| POST | `/api/triggers/webhook/{name}` | **入站事件端点**：两级过滤命中即自动创建 run（配了 secret 需带 `X-Trigger-Secret` 头） |

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

`agentteam/presets/` 提供 8 个开箱即用的团队模板：

| Preset | 文件 | 适用场景 |
|---|---|---|
| `dev_lead` | `dev_lead_team.py` | **程序员领导管理小队**（技术主管 + 程序员 + 测试）：带验收标准的分派（派活即告知交付标准）、交付后机器验收（contains/regex/test → `criteria_check` 事件）、预算告警（`budget_exceeded`）、coder 进专家库参与自进化、写文件/执行命令需人工审批 |
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
- **Teams** — 团队 CRUD、配置编辑、Team Memory 记忆面板（查看/删除团队记忆）
- **Runs** — run 列表、状态过滤
- **RunDetail** — run 详情、SSE 实时轨迹（含追问回执/撤权/授权过期事件）、审批 diff 预览（write_file 变更 diff / bash 命令在批准前直接可见）、追问直通面板（运行中/等待审批时对指定 worker 或 leader 投递追问，禁止广播）、Mandate 授权凭据展示（旧 run 无凭据自动省略）
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

- **741 测试用例 / 93 测试文件**，覆盖所有核心模块（740 passed + 1 skipped 基线）
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

- [x] M1 基础设施层
- [x] M2 领域与编译（Team/Worker/TeamCompiler/LangGraph）
- [x] M3 审批与轨迹
- [x] M4 MCP 集成（子图 ReAct + 工具级审批 + MCP 工具加载）
- [x] M5a API（FastAPI + SSE + RunManager）
- [x] M5b Web UI（React + antd + SSE 实时控制台）
- [x] M6 示例团队 + 测试
- [x] SP7 Skill 系统 + 自进化

## 容器部署（内网一键起）

仓库自带镜像定义与编排：`Dockerfile`（控制平面，React 控制台在镜像内构建后
由 FastAPI 同源挂载）。

```bash
docker compose build
docker compose up -d
docker compose logs -f agentteam   # 控制台 http://127.0.0.1:8000（只绑宿主 loopback）
```

拓扑与安全边界：

- **宿主 `8000` 只绑 `127.0.0.1`**，对外访问交给内网反向代理或 SSH 隧道。
- **非 root 用户**：容器内以 `agentteam`(uid 10001) 运行。
- **代码不出域**：把目标仓库挂成工作目录，worker 的 `write_file` / `bash`
  都在工作目录内发生。
- **模型不出域**：provider 指向内网网关（vLLM / Ollama / 自托管 DeepSeek / LiteLLM），
  密钥经环境变量注入，不要把明文写进 Team JSON。
- **持久化**：`agentteam-data` 命名卷（SQLite：团队、run 快照、审批、审计、
  进化历史、记忆）。

首次起完注册团队：

```bash
python -m agentteam.cli install-preset enterprise_dev
```

## License

MIT
