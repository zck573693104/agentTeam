# SP8：opencode 套壳架构设计（Harness Engine）

> 状态：已定稿并实现。本档是 `agentteam/harness/` 的设计依据。

## 1. 目标

把 AgentTeam 从「自研 LangGraph 执行内核」升级为「控制平面 + opencode 执行底座」：

- **控制平面（保留）**：Team JSON schema、ApprovalPolicy 三级审批语义、AgentLibrary `$ref`、
  团队预设、SP7 自进化、SQLite 审计、FastAPI+SSE API、React 控制台。
- **执行底座（新增）**：[opencode](https://github.com/sst/opencode)（MIT）headless server
  承担 agent loop、工具系统（read/write/bash/grep...）、模型接入（75+ provider）、MCP、
  会话持久化、token/cost 统计。
- **双引擎并存**：`Team.engine = "langgraph" | "opencode"`，默认 langgraph（向后兼容），
  opencode 引擎是一等公民路径。旧引擎零改动、全部测试保持通过。

## 2. 已核实的 opencode 契约（v1.18.32，实测）

来源：`GET /doc` OpenAPI 3.1 + 本机真实冒烟（2026-09-25）。

| 能力 | 端点 | 要点 |
|---|---|---|
| 建会话 | `POST /session` | body: `{title, agent, model:{providerID,modelID}, permission: PermissionRuleset, parentID}` — **会话级权限规则集** |
| 提交任务（同步） | `POST /session/{id}/message` | body: `{parts:[{type:"text",text}], system, agent, model, tools:{name:bool}, format}` — **每次调用可覆盖 system prompt / agent / 工具白名单 / 结构化输出** |
| 提交任务（异步） | `POST /session/{id}/prompt_async` | 同 body，立即返回；完成以 `session.idle` 事件为准 |
| 审批应答 | `POST /session/{id}/permissions/{pid}` | body: `{response:"once"\|"always"\|"reject"}` |
| 全局事件流 | `GET /event`（SSE） | 事件形如 `{id,type,properties}`；关键类型：`session.created`、`message.updated`、`message.part.updated`、`session.idle`、`session.error`、`permission.asked`、`permission.replied`、`session.status` |
| 取消 | `POST /session/{id}/abort` | |
| 动态 MCP | `POST /mcp` | `{name, config:{type:"local",command:[...],environment}\|{type:"remote",url,headers}}` |
| 动态配置 | `PATCH /config` | 可编程注册 provider/agent/mcp/permission |
| 用量 | `GET /session/{id}` | `tokens:{input,output,reasoning,cache}` + `cost` |
| PermissionRule | — | `{permission, pattern, action}`，action ∈ `allow\|deny\|ask` |

## 3. 总体架构

```
┌──────────────────────────────────────────────────────────────┐
│ AgentTeam 控制平面（不变）                                     │
│  domain(Team/ApprovalPolicy/Library)  storage(SQLite 审计)    │
│  api(REST+SSE)  web(React)  evolution(SP7)  presets           │
└───────────────┬──────────────────────────────────────────────┘
                │ agentteam/harness/（新增执行内核）
│  opencode_client.py  REST + SSE（线程安全订阅、自动重连）        │
│  translator.py       Team JSON → opencode 会话/权限/MCP/provider│
│  approval.py         ApprovalBroker：三级审批桥                │
│  engine.py           HarnessRunner：plan→dispatch→review 编排  │
│  events.py           opencode 事件 → AgentTeam trace 事件词表   │
│  runner.py           注册表 + graph 协议适配 + 状态持久化        │
└───────────────┬──────────────────────────────────────────────┘
                │ HTTP 127.0.0.1:<port>
      ┌─────────▼─────────┐
      │  opencode server  │ agent loop / 工具 / 模型 / MCP / 沙箱
      └───────────────────┘
```

### 3.1 graph 协议适配（RunManager 零分支复用）

`HarnessRunner` 实现 LangGraph 图的孪生协议，直接塞进现有 `RunManager.start_run`：

- `invoke(initial_state, config)` → 跑完整个编排；遇审批门时**落盘续跑状态并 return**
  （线程结束，RunManager 据此标 interrupted）。
- `get_state(config)` → 返回 `_State(values, next)`；`next` 非空 ⇔ 有 parked 续跑点。
- resume：`RunManager.resume_run` 检测 runner 类型，harness 走 `invoke({"__resume__": ...})`
  新线程重入，从落盘状态恢复（含**服务重启后**——状态持久化在 SQLite `run_engine_state` 表，
  与 LangGraph 的 SqliteSaver checkpoint 对等）。

### 3.2 编排语义（与 LangGraph 引擎逐点对齐）

| LangGraph 引擎 | Harness 引擎 |
|---|---|
| `leader_plan`（`with_structured_output(Plan)`） | opencode `format:{type:"json_schema", schema:Plan}` 结构化 prompt；dag 环检测/step id 去重重用 `graph.py` 纯函数 |
| step 级审批门（interrupt per dispatch round） | seq：每步 dispatch 前 gate；dag：每轮 ready 批次 dispatch 前 gate（同一轮只门一次，语义一致） |
| worker 级审批门 | worker 会话创建前 gate（targets 匹配才门） |
| tool 级审批（interrupt in tool_step） | worker 会话 `permission` 规则集：targets 内工具 `ask`，其余 `allow`；`permission.asked` 事件 → broker 落盘 + run interrupted + 线程 return；approve 后 resume 先 `POST /permissions/{pid}` 回 `once`/`reject`，再续等同一会话完成（opencode 会话服务端存活） |
| ReAct 循环 | opencode 原生 agent loop（`max_iterations` → agent `steps` 上限近似） |
| skills 注入 `<skill>` SystemMessage | system prompt 拼接同格式 skill 块（SkillLoader 复用） |
| `leader_review`（seq 每步/dag 每轮） | 同节奏的 review prompt（无工具会话），事件词表一致 |
| 取消：cancel_event → agent_step 检查 | dispatch 间隙轮询 + 在飞会话由 watcher 线程 `POST /abort` → `RunCancelledError` |
| total_tokens 汇总 | run 结束时聚合各会话 `session.tokens` |

### 3.3 事件词表（前端零改动）

`events.py` 把 opencode 事件映射为现有 trace 词表，SSEViewer/RunDetail 不改渲染逻辑：

`worker_start`（session.created）、`tool_call`（message.part.updated 且 part.type=="tool"）、
`worker_end`（会话完成）、`leader_plan`、`leader_review`、`approval_requested`/`approval_decided`、
`run_start`/`run_end`/`error`/`run_cancelled`（RunManager 既有逻辑）、`run_interrupted`（控制信号）。

### 3.4 线程与并发

- SSE 监听：`OpenCodeClient` 单条 `GET /event` 长连接 + 守护线程 + 订阅者注册表
  （`subscribe(fn)`），断线自动重连（指数退避），`Event` 语义即「收到即回调」。
- 引擎主线程：顺序/ThreadPool（dag 并行 fan-out）调度 `prompt_async` + 等待
  `session.idle|session.error|park` 信号（`threading.Event` per session）。
- 审批 broker：运行在 SSE 回调线程，**只做落盘 + 置信号**，绝不阻塞 SSE；
  人工决策通过 resume 路径回放。
- 复用全局约定：SQLite 单锁（`AuditRepo` 自带）、EventBus 满丢最旧、
  `RunRepo.try_claim` 原子状态机。

## 4. 权限映射（tool 级审批落进 opencode permission 体系）

AgentTeam 工具名 → opencode 工具名：
`read_file→read`、`write_file→write`、`list_dir→list`、`search_web→webfetch`、
`mcp:{server}:{tool}→{server}_{tool}`（opencode MCP 工具前缀约定）、`bash→bash`。
`ApprovalPolicy(level="tool", targets=[...])` → 会话规则集：targets 命中工具 `ask`、
其余全部 `allow`（含 bash/edit 默认放行与否由 translator 的 `default_tool_policy` 决定，
未声明策略的 worker 维持 opencode 默认）。

## 5. 配置

| 环境变量 | 含义 | 默认 |
|---|---|---|
| `AGENTTEAM_OPENCODE_URL` | opencode server 地址 | `http://127.0.0.1:4096` |
| `OPENCODE_SERVER_PASSWORD` | server Basic Auth（可选） | 无 |
| `AGENTTEAM_OC_MODEL` | 默认模型 `provider/model` | `opencode/ling-3.0-flash-fin-free`（免费冒烟） |
| `AGENTTEAM_DEFAULT_ENGINE` | Team 未声明 engine 时的默认 | `langgraph` |
| `AGENTTEAM_OC_TIMEOUT` | 单次 prompt 完成等待上限（秒） | `600` |

provider 翻译（qwen/deepseek/ollama → opencode provider 配置）见 `translator.py`
`provider_patch_for()`，引擎启动时经 `PATCH /config` 合并注入。

## 6. 测试策略

1. **Fake opencode server**（`tests/harness/fake_opencode.py`）：按上述契约实现
   session/message/permission/SSE/abort 子集，剧本可编程（计划→工具调用→权限请求→
   结构化输出），驱动全部引擎语义的确定性测试。
2. **单元/集成**：translator、client、events、approval（gate/permission/timeout）、
   engine（seq/dag/三级审批/拒绝/取消/进化触发）、runner（重启恢复）、
   API 层（engine=opencode 的团队全流程，镜像既有集成测试）。
3. **真实冒烟**（skip-if-unavailable）：真实 opencode + 免费模型跑通
   建 session→prompt→SSE→审批规则集→abort，以及完整 API E2E
   （`tests/harness/test_real_opencode.py`，探测不到 server 自动 skip）。
4. **回归**：旧引擎全部既有测试保持通过（SP8 收尾时 669 全绿）。

## 7. 真实 opencode v1.18.32 实测的已知问题（重要）

以下为 2026-09-25 对真实 server 逐项实测的结论，直接影响本引擎的实现取舍：

| 特性 | 预期（按 OpenAPI/文档） | 实测 | 引擎对策 |
|---|---|---|---|
| per-request `tools` 参数 | 启用/禁用工具 | **传任何 tools map 会静默杀死回合**（1.5s 返回空 parts，会话卡死） | 引擎完全不传；白名单退化为 prompt 级指令（`translator.worker_tool_directive`） |
| permission `deny` 动作 | 自动拒绝 | **含 deny 规则的会话连普通 prompt 都挂起**（与是否调用工具无关） | 规则集只使用 `ask`/`allow`；deny 不下发 |
| permission `ask` 动作 | 等待人工 | ✅ 正常：回合阻塞、`GET /permission` 可见、回帖后继续 | tool 级审批的核心机制，依赖此 |
| `PATCH /config` 注册 agent | 新 agent 可用 | 静默不生效 | 不依赖 |
| `.opencode/agent/*.json` 文件 | 注册并强制 tools | 会话接受 agent 名但**工具限制不生效** | 不依赖 |
| `format: json_schema` 结构化输出 | 文本返回 JSON | ✅ 可用，但：① schema 必须 TypeBox 兼容（`type` 数组/`$defs` 会 400，Plan schema 手写）；② JSON 经 `StructuredOutput` 工具调用返回，text part 近空 | `_PLAN_SCHEMA` 手写；`_prompt_text` 从工具入参兜底提取 |
| `POST /session` 的 `model` | `{providerID, modelID}` | **要求 `{providerID, id}`**（与 prompt 级不同，additionalProperties=false） | client 做字段转换 |
| sync `POST /session/{id}/message` | 阻塞到回合结束 | ✅ 但**审批 pending 时也阻塞**（引擎自己的线程会被吊住） | 引擎统一用 `prompt_async` + 轮询 |
| v2 `POST /api/session/{id}/wait` | 等待 idle | 返回 503 "not available yet" | 不依赖；用 `session.idle` 事件 + 消息轮询 |
| SSE `GET /event` | 实时事件流 | ✅ 正常；注意 Python `iter_lines()` 会缓冲满 chunk 才返回，必须用 `raw.read1()` | client `_iter_sse` 已处理 |

结论：**审批语义（ask 规则）与结构化输出在真实 server 上完整可用**；
工具白名单在 v1.18.32 上只能 prompt 级近似（模型自行遵守），待 opencode
修复 `tools`/`deny` 后，translator 已预留契约位（`OPENCODE_BUILTIN_TOOLS`），
开启 `AGENTTEAM_OC_STRICT_TOOLS=1` 即可下发 deny 规则（未来版本生效）。
