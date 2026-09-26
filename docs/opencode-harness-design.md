# SP8：opencode 套壳架构设计（Harness Engine）

> 状态：已定稿并实现，并已迁移到 opencode **v2 `/api/*` 面**（§8）。
> 本档是 `agentteam/harness/` 的设计依据；§2 是 v2 现行契约，§7 保留 v1 实测史
> （解释为什么某些能力不再依赖）。

## 1. 目标

把 AgentTeam 从「自研 LangGraph 执行内核」升级为「控制平面 + opencode 执行底座」：

- **控制平面（保留）**：Team JSON schema、ApprovalPolicy 三级审批语义、AgentLibrary `$ref`、
  团队预设、SP7 自进化、SQLite 审计、FastAPI+SSE API、React 控制台。
- **执行底座（新增）**：[opencode](https://github.com/sst/opencode)（MIT）headless server
  承担 agent loop、工具系统（read/write/bash/grep...）、模型接入（75+ provider）、MCP、
  会话持久化、token/cost 统计。
- **双引擎并存**：`Team.engine = "langgraph" | "opencode"`，默认 langgraph（向后兼容），
  opencode 引擎是一等公民路径。旧引擎零改动、全部测试保持通过。

## 2. 已核实的 opencode 契约（v2 `/api/*`，v1.18.32 实测）

来源：`GET /doc` OpenAPI 3.1（`/api/*` 面 51 个端点）+ 本机真实冒烟
（2026-09-25，`.tmp_oc_smoke/probe_v2*.py`）。响应统一包一层 `{"data": ...}`。

| 能力 | 端点 | 要点 |
|---|---|---|
| 建会话 | `POST /api/session` | body `{id?, agent?, model:{id, providerID, variant?}, location?}` — **无 title、无 permission 规则集**；`agent` 接受未注册名（原样回显），引擎用它携带 worker 名作会话标签 |
| 提交任务 | `POST /api/session/{id}/prompt` | body `{prompt:{text,files,agents}, delivery:"steer"\|"queue", resume:bool}` — **异步唯一形态**；无 per-request `system`/`format`/`tools`/`model`；返回 `data.id` = 本轮用户消息 id（完成判定的锚点） |
| 消息 | `GET /api/session/{id}/message` | `{data:[{id,type:"user"\|"assistant",finish,content:[{type:"text"\|"reasoning"\|"tool"}],tokens,snapshot:{start,end,files}}], cursor}` — **按时间倒序**；`finish:"stop"` 回合结束、`"tool-calls"` 表示 agent loop 继续 |
| 事件重放 | `GET /api/session/{id}/history?limit=100` | durable 事件（`durable:{aggregateID,seq,version}`），与 SSE 同构；**limit 上限 100**（200 → 400） |
| 全局事件流 | `GET /api/event`（SSE） | `{id, type:"session.next.*", durable?, location, data}` — **载荷在 `data`**（v1 是 `properties`）；除 `*.delta` 外均带 `durable.seq`，与 history 是同一条事件的两份副本 |
| 中断 | `POST /api/session/{id}/interrupt` | 取代 v1 abort |
| 权限 | `GET /api/permission/request`、`POST /api/session/{id}/permission/{rid}/reply` | v2 形态 `{action, resources}`；**v2 会话实测不产生 pending**（见 §8） |
| 用量 | `GET /api/session/{id}` | `tokens` 聚合**恒为 0** → 只能逐消息 `tokens` 求和 |
| 配置 / MCP | 无 v2 端点 | provider 补丁与 MCP 挂载仍走 v1 `PATCH /config` / `POST /mcp` |

事件词表（实测一次带工具的回合）：`prompt.admitted → prompted → step.started →
tool.input.started(name) → tool.input.delta* → tool.input.ended(完整入参) →
tool.called → tool.success → step.ended(finish:"tool-calls") → step.started →
text.* → step.ended(finish:"stop")`。多步回合天然给出两次中断窗口。

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

| LangGraph 引擎 | Harness 引擎（v2） |
|---|---|
| `leader_plan`（`with_structured_output(Plan)`） | v2 无结构化输出通道：`translator.plan_prompt()` 把 JSON Schema 内联进 prompt + 硬约束「只输出一个 JSON 对象」，`translator.extract_json()` 宽容提取（整体→```围栏→首个平衡花括号）；dag 环检测/step id 去重重用 `graph.py` 纯函数 |
| step 级审批门（interrupt per dispatch round） | seq：每步 dispatch 前 gate；dag：每轮 ready 批次 dispatch 前 gate（同一轮只门一次，语义一致）。dag 依赖满足与空轮 parity：**skipped 视为依赖满足**、空就绪集返回 END（对齐 `graph.make_route_from_plan_dag`），否则被跳过的 step 会阻塞后继造成 round_gate/review 空转 |
| worker 级审批门 | worker 会话创建前 gate（targets 匹配才门） |
| tool 级审批（interrupt in tool_step） | **事后中断 + 审计**（v2 无会话级规则集、不产生 pending permission）：观测到工具调用 → 判定 → `POST interrupt` 掐断回合 → `tool_denied` 审计 + park；放行则向同一会话补发 `approved_resume_prompt` 续跑，拒绝则整步 rejected。详见 §4 |
| ReAct 循环 | opencode 原生 agent loop（`max_iterations` → agent `steps` 上限近似） |
| skills 注入 `<skill>` SystemMessage | v2 无 per-request system：角色设定 + skills 块 + 工具白名单指令**内联进用户文本**（`translator.worker_user_prompt`，分隔线区隔指令与任务） |
| `leader_review`（seq 每步/dag 每轮） | 同节奏的一次性 prompt（`_one_shot`），事件词表一致 |
| 取消：cancel_event → agent_step 检查 | dispatch 间隙轮询 + 在飞会话 `POST /api/session/{id}/interrupt` → `RunCancelledError` |
| total_tokens 汇总 | 逐消息 `tokens` 求和（v2 会话聚合恒 0），按会话记已累计值保证幂等 |

### 3.3 事件词表（前端零改动）

`events.py` 把 v2 `session.next.*` 事件映射为现有 trace 词表，SSEViewer/RunDetail 不改渲染逻辑：

`worker_start`/`worker_end`/`leader_plan`/`leader_review`/`approval_requested`/
`approval_decided`/`tool_denied`（引擎直接 emit，与 LangGraph 引擎同源同形）、
`tool_call`（`tool.input.started`/`tool.called` → 每个 callID 只 emit 一次）、
`run_start`/`run_end`/`error`/`run_cancelled`（RunManager 既有逻辑）、
`run_interrupted`（控制信号）。

`EventMapper` 还是「观测到的工具调用」的仓库（`observed_calls` / `take_new_calls` /
`requeue_calls`）：SSE 与 `history()` 是同一条 durable 事件的两份副本，
按事件标识（`id`，缺失时 `durable.seq`）去重 —— **不能用 seq 高水位**，
跨线程投递会乱序，后到的低 seq 工具事件会被误判为重复而整批丢失。

### 3.4 线程与并发

- SSE 监听：`OpenCodeClient` 单条 `GET /api/event` 长连接 + 守护线程 + 订阅者注册表
  （`subscribe(fn)`），断线自动重连（指数退避）。
- 引擎主线程：顺序/ThreadPool（dag 并行 fan-out）`prompt_async` + 轮询
  `assistant_done(session_id, turn_id)`；SSE 只用来少睡一轮，**run 的正确性不依赖 SSE**
  （完成前用 `history()` 对账补齐工具观测，见 §3.3）。
- 回合锚点：完成判定只看「本轮用户消息之后」的 assistant —— 否则放行后补发 prompt 时，
  上一轮的 `finish:"stop"` 会被误读成已完成。
- 审批 broker：只做落盘 + 抛 `ApprovalInterrupt`，绝不阻塞 SSE 回调线程；
  人工决策通过 resume 路径回放。
- 复用全局约定：SQLite 单锁（`AuditRepo` 自带）、EventBus 满丢最旧、
  `RunRepo.try_claim` 原子状态机。

## 4. 权限映射（tool 级审批 = 事后中断 + 审计）

AgentTeam 工具名 → opencode 工具名：
`read_file→read`、`write_file→write`、`list_dir→list`、`search_web→webfetch`、
`mcp:{server}:{tool}→{server}_{tool}`（opencode MCP 工具命名约定）、`bash→bash`。
匹配域两侧同收（`worker_tool_whitelist` / `agentteam_tool_targets` 都返回
「AgentTeam 名 ∪ opencode 名」），事件里出现哪一侧都能判定。

v2 既没有会话级 PermissionRuleset，也不产生 pending permission（§8），所以
`ApprovalPolicy(level="tool", targets=[...])` 从「事前拦截」改为「事后处置」：

| 违规成因 | 触发条件 | 处置 |
|---|---|---|
| `requires_approval` | 命中 tool 级 `targets`（`None` = 通配 `*`） | interrupt + `tool_denied` 审计 + park 等人工；`timeout_seconds` 已设则自动放行（decider="timeout"） |
| `not_whitelisted` | 调用了 `Agent.tools` 白名单外的工具 | 由 `AGENTTEAM_OC_TOOL_GUARD` 决定：`strict`（默认）interrupt+park / `audit` 只记事实继续跑 / `off` 完全不查 |

`todowrite`/`todoread`/`task` 属计划簿记，不参与执法（`NON_TOOL_NAMES`）。
已放行的工具记在快照 `approved_tools[session_id]`，同工具二次调用不再 park。

**已知代价（用户确认接受的退化）**：中断是事后的，第一个被观测到的调用可能已经执行完毕；
控制平面保证的是「不再继续 + 事实入账」，不是「未执行」。

## 5. 配置

| 环境变量 | 含义 | 默认 |
|---|---|---|
| `AGENTTEAM_OPENCODE_URL` | opencode server 地址 | `http://127.0.0.1:4096` |
| `OPENCODE_SERVER_PASSWORD` | server Basic Auth（可选） | 无 |
| `AGENTTEAM_OC_MODEL` | 默认模型 `provider/model` | `opencode/ling-3.0-flash-fin-free`（免费冒烟） |
| `AGENTTEAM_DEFAULT_ENGINE` | Team 未声明 engine 时的默认 | `langgraph` |
| `AGENTTEAM_OC_TIMEOUT` | 单次 prompt 完成等待上限（秒） | `600` |
| `AGENTTEAM_OC_TOOL_GUARD` | 白名单外工具调用的处置：`strict`/`audit`/`off`（§4） | `strict` |

provider 翻译（qwen/deepseek/ollama → opencode provider 配置）见 `translator.py`
`provider_patch_for()`，引擎启动时经 v1 `PATCH /config` 合并注入（v2 无配置端点）；
api key 一律以 `{env:VAR}` 引用，不落盘明文。

## 6. 测试策略

1. **Fake opencode server**（`tests/harness/fake_opencode.py`）：按 v2 契约实现
   session/prompt/message(倒序)/history(durable.seq)/interrupt/permission/SSE 子集，
   剧本可编程（计划→工具调用→中断窗口→续跑），驱动全部引擎语义的确定性测试。
   **忠实度是本文件的命门**：`resume=false` 只入队不调度、`history?limit>100` 报 400、
   事件带 `durable.seq`、interrupt 后剩余步骤在下一次 prompt 继续 —— 都是真实 server
   实测行为被踩坑后回填进 fake 的。
2. **单元/集成**：translator、client、events、approval（gate/事后中断/timeout/三种
   guard 模式）、engine（seq/dag/三级审批/拒绝/取消/白名单/重启恢复/嵌套 supervisor/
   TeamRef 注册表解析与 fail-fast/dag condition 跳步/dag 轮内工具审批 park/resume）、
   runner（ensure_backend 的 provider+MCP 幂等引导）、
   API 层（engine=opencode 团队全流程 + 示例团队 E2E）。
   fake server 用 HTTP/1.1 keep-alive：1.0 下引擎 0.02s 轮询会产生数千短连接，
   Windows 上耗尽临时端口（WinError 10048），且整套件慢约 5 倍。
3. **真实冒烟**（skip-if-unavailable，`tests/harness/test_real_opencode.py`）：
   建会话（未注册 agent 标签）→ prompt（turn 锚点）→ `finish:"stop"` → 逐消息 tokens →
   倒序消息 + history seq → SSE `data` 载荷 → v2 契约形状（`model.id`）→ 完整 API E2E。
   探测 `AGENTTEAM_OPENCODE_URL` / `:4117` / `:4096`，全不通则整文件 skip。
   这一层不可省略：**fake 宽容、真 server 严格的差异只能在这里暴露**
   （§8 的致命项都由它抓出）。
   反过来它也不能当功能保证：`opencode/ling-3.0-flash-fin-free` 是共享额度
   模型，连发真实请求必撞 `HTTP 429 FreeUsageLimitError`，所以冒烟层把带
   限流特征的失败按 **skip** 上报（`_blame_upstream`），功能保证由离线层负责。
   换 `AGENTTEAM_OC_MODEL` 指向企业自有 provider 后这条限流就不存在了。
4. **回归**：旧引擎全部既有测试保持通过。

## 7. v1（`/session` 面）实测的已知问题（历史，已由 v2 迁移取代）

2026-09-25 对 v1 面逐项实测的结论，解释了为什么下列能力不再被依赖：

| 特性 | 预期（按 OpenAPI/文档） | 实测 | 引擎对策 |
|---|---|---|---|
| per-request `tools` 参数 | 启用/禁用工具 | **传任何 tools map 会静默杀死回合**（1.5s 返回空 parts，会话卡死） | 白名单退化为 prompt 级指令 + 事后中断（§4） |
| permission `deny` 动作 | 自动拒绝 | **含 deny 规则的会话连普通 prompt 都挂起** | 不再下发 deny |
| permission `ask` 动作 | 等待人工 | ✅ 正常：回合阻塞、`GET /permission` 可见、回帖后继续 | v2 无此能力 → 事后中断 |
| `PATCH /config` 注册 agent | 新 agent 可用 | 静默不生效 | 不依赖 |
| `.opencode/agent/*.json` 文件 | 注册并强制 tools | 会话接受 agent 名但**工具限制不生效** | 不依赖 |
| `format: json_schema` | 文本返回 JSON | ✅ 可用（JSON 走 `StructuredOutput` 工具入参） | v2 无此通道 → prompt + `extract_json` |
| sync `POST /session/{id}/message` | 阻塞到回合结束 | ✅ 但**审批 pending 时也阻塞** | 统一异步 + 轮询 |
| SSE `GET /event` | 实时事件流 | ✅；`iter_lines()` 会缓冲满 chunk 才返回，必须 `raw.read1()` | client `_iter_sse` 已处理 |

## 8. v2 迁移：能力退化清单与两个致命项

用户决策（2026-09-25）：**全量迁 v2、接受能力退化**，tool 级审批改为
「事后中断 + 审计」。以下为 v1.18.32 `/api/*` 面逐项实测结论。

### 8.1 两个致命项（都是「HTTP 200、无报错、静默不干活」）

| 项 | 现象 | 根因与对策 |
|---|---|---|
| `resume` 语义 | `POST /api/session/{id}/prompt` 带 `resume:false` 时：`200` + `prompt.admitted` 事件，但回合**永不被调度**，消息列表停在 user，`/api/session/active` 恒空，**无任何报错** | spec 原文「schedule agent-loop execution **unless resume is false**」。客户端默认值曾是 `False`（照抄 v1 心智）→ 全链路挂死。改为默认 `True`，fake server 忠实复现该语义 + `test_resume_false_admits_but_never_schedules` 钉住 |
| 完成信号 vs 工具观测竞态 | REST 已 `finish:"stop"` 而 SSE 的 `tool.input.*` 还没到 → 事后拦截整批漏观测（实测约 1/12 概率漏 park） | 收尾前用 `history()`（durable、REST、与消息同序）对账补齐观测再判违规；重放按事件标识去重（§3.3）。`test_tool_gate_parks_even_when_sse_lags` 用 fake 的 `blackhole`（只进 history 不推 SSE）钉住 |

顺带纠正一条早先的误判：v2 探针里「工具执行坏掉（`Unable to execute command`）」
其实也是 `resume:false` 引起的 —— 修好后真实 v2 会话的工具调用链路
`tool.input.started → ended → called → success` 完整可用。

### 8.1.1 第三种静默形态：干净收尾的空回合（上游抖动，非契约缺陷）

实测（1.18.32 + 免费模型 `opencode/ling-3.0-flash-fin-free`，40 轮里 2 轮、
12 轮里 2 轮 ≈ 5%）：回合以 `step.ended finish:"stop"` 正常结束，但
assistant 消息 **没有任何 text part**：

```
msg type=assistant finish=stop error=null
    tokens={'input':3,'output':0,'reasoning':36,'cache':{'read':3328,'write':0}}
    content=[]
```

即模型把预算全花在 reasoning 上、可见文本为空（`output:0`）。`/api/message`
里没有错误字段，SSE 也只有正常的 `step.ended` —— 对引擎而言「回合完成了」
和「回合什么也没说」是两件事。

对策：`HarnessRunner._one_shot` 在拿到空文本时**换一个全新会话重问一次**
（`_ask_once` 拆分出来就是为了复用「建会话→prompt→轮询→取文本」这段）。
换会话而不是同会话重问是实测结论：同一会话里上一段空回合（连同它的
reasoning）会进上下文，连空两次的概率远高于独立事件，同会话重问后
E2E 冒烟仍然约每 5 次挂 1 次；改换全新会话后才消失。
免费模型上这让控制面一次调用的失败率从 ~5% 降到可忽略；重问仍空则按
「计划非法」失败，信息不变。只覆盖控制面（plan/review），worker 回合不重试 ——
worker 回合可能带工具副作用，重放不等于重试，且旧引擎同样不重试。

fake server 用 `Script.empty_turns = N` 忠实复现（前 N 次 leader 回合
`finish=stop` + `content=[]`），钉住
`test_empty_plan_turn_retried_once` / `test_two_empty_plan_turns_fail_run`。

同一压力下还观测到另一种形态：**上游限流的回合根本不算「完成」**。

实测（连发真实请求撞 opencode zen 免费额度）失败回合的形状是：

```
SSE:  session.next.step.failed
      {assistantMessageID, error:{type:"unknown",
        message:"Provider request failed with HTTP 429: {...FreeUsageLimitError...}"}}
REST: assistant 消息 finish=="error"、content=[]、error.message 同上
```

注意**没有** `session.error` 事件，也**没有** `step.ended` —— 而
`assistant_done()` 只认 `finish=="stop"`。所以迁移前这条路径的表现是：
引擎一路轮询到 `AGENTTEAM_OC_TIMEOUT`（默认 600s）才失败，而且失败原因
被报成「Leader plan is not valid JSON: empty answer」，完全指错方向。

对策（两层，与「正确性不依赖 SSE」的既定原则一致）：
- `events.py` 接 `session.next.step.failed` → 与 `session.error` 同样落
  `session_error`（SSE 快路径）。
- `opencode_client.turn_error()` 读 REST 侧 `finish=="error"` 的
  `error.message`；`_ask_once` / `_wait_sessions` 两条等待循环都查它
  （SSE 全黑时仍能几秒内失败）。
fake 侧用 `Script.turn_errors = {agent 名: 错误文本}` 复现该形状，
`test_throttled_plan_turn_reports_provider_error`（SSE 路径）与
`test_throttled_worker_turn_fails_fast_without_sse`（REST 兜底 + blackhole）钉住。

冒烟层的处置：免费额度限流是**配额问题不是套壳缺陷**，
`test_real_opencode.py::_blame_upstream` 认得 `429 / FreeUsageLimitError /
Rate limit` 这类文本并按 skip 处理（离线 90+ 例才是功能保证）。

### 8.2 能力退化（v1 → v2）与对策

| v1 能力 | v2 现状 | 对策 |
|---|---|---|
| per-request `system` | 无 | 角色设定内联进用户文本（`prompt_with_system`） |
| per-request `format` 结构化输出 | 无 | schema 写进 prompt + `extract_json` 宽容提取 |
| 会话级 `permission` 规则集 | 无（`POST /api/session/{id}/permission` 是「发起请求」不是「设规则」；v2 会话不产生 pending） | tool 级审批改事后中断 + 审计（§4） |
| per-request `tools` 白名单 | 无 | prompt 级指令 + 事后拦截（两层） |
| `GET /session/{id}` token 聚合 | 恒 0 | 逐消息 `tokens` 求和（幂等） |
| `POST /session/{id}/abort` | 改名 | `POST /api/session/{id}/interrupt` |
| `PATCH /config` / `POST /mcp` | 无 v2 端点 | 继续走 v1 路径（runner 里注明） |
| `POST /api/session/{id}/wait` | **503 不可用** | 轮询 `assistant_done` |

### 8.3 v2 变强的地方

- durable 事件流（`seq`/`version`）可重放 → 重启后能补审计、能事后对账。
- 逐消息 `tokens` + `step.ended{cost,snapshot,files}` → 用量与文件改动可按回合归因。
- 工具调用分阶段可见（`input.started` 就给出工具名，`input.ended` 给完整入参）
  → 事后拦截能在入参成形时判定，比 v1 的「part 完成后才知道」更早。
- `agent` 字段接受任意标签 → 会话可按 worker 归因（真实 CLI/TUI 亦复用此面）。
