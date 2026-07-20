# AgentTeam

> 本地多智能体治理与协作框架，对标阿里云 [AgentTeams](https://www.aliyun.com/product/agentteams) 产品形态，基于 Python + LangGraph 构建。

一个把"多智能体编排 + 企业级治理 + Web 控制台"三件事做到开箱即用的本地框架。无需云依赖，单进程即可运行完整的团队注册、任务执行、审批续跑、Skill 供应链、PEP 零信任拦截、配额告警、多维监控等能力。

---

## 目录

- [核心特性](#核心特性)
- [架构总览](#架构总览)
- [快速开始](#快速开始)
- [配置](#配置)
- [API 参考](#api-参考)
- [CLI 命令](#cli-命令)
- [Skill 系统](#skill-系统)
- [治理能力](#治理能力)
- [预置团队](#预置团队)
- [Web 控制台](#web-控制台)
- [测试](#测试)
- [模块索引](#模块索引)
- [里程碑](#里程碑)

---

## 核心特性

### 编排能力

- **递归 Agent 树**：支持任意层级 supervisor / worker 嵌套，单 Worker 也能跑
- **两种执行模式**：sequential（顺序执行 plan）和 dag（依赖图并行执行，含条件分支）
- **多模型供应商**：Qwen / OpenAI / Anthropic / Ollama 统一抽象，按 Agent 粒度绑定模型
- **MCP 集成**：基于 Model Context Protocol 加载外部工具，支持 stdio / http 传输
- **Agent 库**：`$ref` 引用复用，循环引用检测，深拷贝 + 字段覆盖
- **自进化引擎**：基于 run 历史自动优化 prompt / max_iterations / approval_policy

### 治理能力（对标阿里云 AgentTeams）

| 能力 | 实现 |
|---|---|
| **RBAC 访问控制** | 4 内置角色（admin / manager / team_admin / user），权限矩阵 `<resource>:<verb>` 命名 |
| **WAT 双身份** | Token 同时绑定 workload 身份与触发用户身份，操作全程可追溯 |
| **Trace 三链** | call（运行生命周期）/ tool（工具调用 + 审批）/ decision（leader plan/review + dag 条件） |
| **PEP 零信任** | AWS IAM 风格策略评估，显式 deny 优先，无匹配默认拒绝 |
| **Skill 供应链** | visibility（public/private/protected）+ per-consumer ACL + 紧急吊销 |
| **MCP Server 鉴权** | 支持 api_key / bearer / basic / oauth2 多种鉴权模式 |
| **配额告警** | 三级状态（ok / warned / blocked），warn_threshold 预警阈值 |
| **多维监控仪表盘** | by_status / by_team / by_chain / top_tools / tokens_by_team 等多维聚合 |
| **审计时间检索** | 管理操作审计支持 start_time / end_time / event_type 范围查询 |

### 工程能力

- **SQLite 持久化**：WAL 模式 + 集中 conn_lock，幂等 schema 迁移框架（PRAGMA user_version）
- **SSE 实时推送**：run 执行轨迹实时流，断线重连支持
- **Checkpoint 续跑**：interrupted run 服务重启后 lazy recompile + 从 checkpoint 续跑
- **凭证加密**：AES-GCM 加密 MCP Server 凭证，主密钥从环境变量注入
- **Webhook 集成**：审批请求可推送至钉钉 / 飞书 / 企业微信 IM bot
- **配置集中化**：pydantic-settings 统一管理所有 `AGENTTEAM_*` 环境变量

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
│  │  auth    │ │  runs    │ │  teams   │ │  admin   │ │dashboard│ │
│  │ RBAC+Key │ │ SSE+审批 │ │  CRUD    │ │ PEP/Quota│ │ 多维聚合│ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └────────┘ │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────────┐ │
│  │  skills  │ │ library  │ │evolution │ │     run_manager      │ │
│  │ 供应链    │ │ Agent库  │ │ 自进化   │ │ 后台线程+interrupt/resume│ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────────────┘ │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────┴──────────────────────────────────┐
│                       Runtime (runtime/)                          │
│   TeamCompiler ─► StateGraph ─► leader_plan → worker ReAct        │
│                  → leader_review（含审批门 + PEP 拦截）             │
│   SkillLoader · TraceWriter · EvolutionEngine · PEPRepo           │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────┴──────────────────────────────────┐
│                       Domain (domain/)                            │
│   Team · Agent · Worker · Leader · ApprovalPolicy · MCPServer     │
│   AgentLibrary（$ref 引用复用 + 循环检测）                          │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────┴──────────────────────────────────┐
│                      Storage (storage/)                           │
│   SQLite + WAL + 集中 conn_lock + 幂等迁移框架(v1~v7)              │
│   runs · run_events · approvals · teams · library_agents          │
│   evolution_history · admin_events · quotas · users · roles       │
│   permissions · skills · skill_acls · pep_policies                │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────┴──────────────────────────────────┐
│                      Models (models/)                            │
│   Qwen · OpenAI · Anthropic · Ollama 统一 ModelProvider 抽象       │
└──────────────────────────────────────────────────────────────────┘
```

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
# 注册内置研发小队
agentteam register-dev-team

# 注册自定义 Team 配置文件
agentteam register-team path/to/team.py
```

### 提交任务

```bash
curl -X POST http://localhost:8000/api/runs \
  -H "Content-Type: application/json" \
  -d '{"team_name": "dev_team", "task": "实现一个 hello world 程序"}'
```

### 查看实时轨迹

```bash
# SSE 实时事件流
curl -N http://localhost:8000/api/runs/{run_id}/stream
```

或浏览器打开 http://localhost:8000 进入 Web 控制台查看。

### 审批续跑

当 Leader step 级或 Worker tool 级审批触发时，run 状态变为 `interrupted`：

```bash
curl -X POST http://localhost:8000/api/runs/{run_id}/approve \
  -H "Content-Type: application/json" \
  -d '{"approved": true, "reason": "同意"}'
```

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
| `AGENTTEAM_AUTH_API_KEYS` | `""` | 合法 API Key 列表，逗号分隔（legacy，无 RBAC） |
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
| POST | `/api/runs/{run_id}/approve` | 审批续跑（approved=true/false） |
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

### Admin 管理面

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/admin/reload` | 从 DB 重载内存缓存 |
| GET | `/api/admin/audit` | 管理操作审计（支持 resource/actor/event_type/start_time/end_time） |
| GET | `/api/admin/quotas` | 列出所有配额 |
| PUT | `/api/admin/quotas/{team_name}` | 设置/更新配额（含 warn_threshold） |
| DELETE | `/api/admin/quotas/{team_name}` | 删除配额 |
| GET | `/api/admin/pep` | 列出所有 PEP 策略 |
| PUT | `/api/admin/pep/{name}` | 创建/更新 PEP 策略 |
| DELETE | `/api/admin/pep/{name}` | 删除 PEP 策略 |
| POST | `/api/admin/pep/evaluate` | 评估策略（principal/action/resource） |
| GET | `/api/admin/skills` | 列出 Skill 元数据 |
| GET | `/api/admin/skills/{name}` | 查看 Skill 元数据 |
| PUT | `/api/admin/skills/{name}` | 创建/更新 Skill 元数据（visibility/version/owner） |
| DELETE | `/api/admin/skills/{name}` | 删除 Skill 元数据 |
| POST | `/api/admin/skills/{name}/revoke` | 紧急吊销 Skill |
| GET | `/api/admin/skills/{name}/acls` | 列出 Skill 的 ACL |
| PUT | `/api/admin/skills/{name}/acls/{team_name}` | 授予 team 使用 Skill 的权限 |
| DELETE | `/api/admin/skills/{name}/acls/{team_name}` | 撤销 team 使用 Skill 的权限 |

### Dashboard 仪表盘

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/dashboard` | 基础统计（total_runs/total_tokens/by_status/by_team/recent_runs） |
| GET | `/api/dashboard/multi_dim` | 多维统计（含 by_chain/top_tools/tokens_by_team） |

### Evolution 自进化

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/evolution/history` | 自进化历史记录 |
| POST | `/api/evolution/rollback/{id}` | 回滚某次自进化变更 |

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

### Skill 供应链治理

通过 `/api/admin/skills` 管理 Skill 元数据：

- **visibility**：`public`（默认公开）/ `private`（仅 owner_team）/ `protected`（按 ACL 授权）
- **status**：`draft` / `published` / `deprecated` / `revoked`
- **ACL**：`protected` 模式下，只有 `skill_acls` 表中授权的 team 才能使用
- **紧急吊销**：`POST /api/admin/skills/{name}/revoke` 立即阻断所有调用

未注册的 Skill 一律拒绝加载（防影子 skill 攻击）。

---

## 治理能力

### RBAC 访问控制

4 内置角色 + `<resource>:<verb>` 权限命名：

| 角色 | 作用域 | 典型权限 |
|---|---|---|
| `admin` | 全局通配 `*` | 所有操作 |
| `manager` | 全局 | team/run/quota/skill 管理 |
| `team_admin` | 单 team 上下文 | 仅对绑定的 team 有管理权 |
| `user` | 全局 | run 提交、查看 |

API Key 与 user 绑定，sha256 哈希存储（不留明文）。请求经 `AuthMiddleware` 校验后注入 `request.state.user`，路由通过 `require_permission(action, team_name_from=...)` 装饰器检查权限。

### WAT 双身份

每次 run 启动时，Token 同时绑定：
- **workload 身份**：执行该 run 的 Agent team
- **user 身份**：触发该 run 的人类用户（`runs.triggered_by_user`）

所有审计事件可追溯到双重身份，满足企业合规要求。

### Trace 三链结构

`run_events.chain` 字段区分三种链路：

| 链 | 事件类型 |
|---|---|
| `call` | run_start / run_end / run_cancelled / worker_start / worker_end / supervisor |
| `tool` | tool_call / tool_result / approval_requested / approval_decided |
| `decision` | leader_plan / leader_review / condition_eval / plan_rejected |

`GET /api/runs/{run_id}/trace?chain=tool` 按链过滤查询，`/api/dashboard/multi_dim` 的 `by_chain` 字段返回三链分布。

### PEP 零信任拦截

策略存储于 `pep_policies` 表，四元组 `(principal, action, resource, effect)` 描述：

```python
# 示例:允许 coder agent 调用 read_file 工具
pep_repo.upsert_policy(
    name="coder-can-read",
    effect="allow",
    principal="coder",
    action="tool:invoke",
    resource="read_file",
)

# 示例:拒绝所有 agent 调用 delete_file
pep_repo.upsert_policy(
    name="deny-delete",
    effect="deny",
    principal="*",
    action="tool:invoke",
    resource="delete_file",
)
```

评估规则（类 AWS IAM）：
1. 收集所有 principal/action/resource 三元组都匹配的策略
2. 任一 `deny` → 拒绝（显式 deny 优先）
3. 有 `allow` 且无 `deny` → 放行
4. 无任何匹配 → **默认拒绝**（零信任）

PEP 在 `make_tool_step` 中对每个 `tool_call` 逐个评估，拒绝的 tool 返回 `ToolMessage` 拒绝响应并过滤，不让 LLM 重试。

### MCP Server 鉴权

`MCPServer` 数据类支持 5 种鉴权模式：

```python
from agentteam.domain.mcp_server import MCPServer

mcp = MCPServer(
    name="internal-api",
    command="...",
    auth_type="bearer",          # none/api_key/bearer/basic/oauth2
    auth_credential="xxx",       # 凭证(加密存储)
    auth_header_name="Authorization",  # api_key 模式可自定义 header
)
mcp.build_auth_headers()  # {'Authorization': 'Bearer xxx'}
```

凭证通过 `agentteam.security.crypto` AES-GCM 加密存储，主密钥从 `AGENTTEAM_SECRET_KEY` 读取。

### 配额告警

每 team 可独立设置 Token 配额：

```bash
curl -X PUT http://localhost:8000/api/admin/quotas/dev_team \
  -H "Content-Type: application/json" \
  -d '{
    "token_limit": 1000000,
    "warn_threshold": 800000,
    "period_seconds": 86400,
    "description": "dev_team 每日 100 万 token 上限"
  }'
```

三级状态：
- `ok`：used < warn_threshold
- `warned`：warn_threshold ≤ used < token_limit（仍允许调用，预警）
- `blocked`：used ≥ token_limit（拒绝新 run，返回 429）

### 多维监控仪表盘

`GET /api/dashboard/multi_dim` 一次返回所有维度：

```json
{
  "total_runs": 120,
  "total_tokens": 4500000,
  "by_status": {"completed": 100, "failed": 5, "interrupted": 15},
  "by_team": {"dev_team": 80, "data_team": 40},
  "tokens_by_team": {"dev_team": 3000000, "data_team": 1500000},
  "by_chain": {"call": 240, "tool": 850, "decision": 120},
  "top_tools": [{"name": "read_file", "count": 320}, ...]
}
```

### 审计时间范围检索

管理操作审计（`admin_events` 表）支持时间范围 + 事件类型过滤：

```
GET /api/admin/audit?event_type=team_created&start_time=2026-07-01T00:00:00Z&end_time=2026-07-31T23:59:59Z
```

---

## 预置团队

`agentteam/presets/` 提供 4 个开箱即用的团队模板：

| Preset | 文件 | 适用场景 |
|---|---|---|
| `enterprise_dev` | `enterprise_dev.py` | 企业研发团队（leader + coder + tester + reviewer） |
| `customer_support` | `customer_support.py` | 客户支持团队（triage + resolver + escalator） |
| `data_analysis` | `data_analysis.py` | 数据分析团队（query + visualize + report） |
| `content_marketing` | `content_marketing.py` | 内容营销团队（researcher + writer + editor） |

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
- **RunDetail** — run 详情、SSE 实时轨迹、审批操作
- **Skills** — Skill 列表、内容查看

### 开发

```bash
cd web
npm install
npm run dev    # 开发模式(http://localhost:5173)
npm run build  # 生产构建(产物到 web/dist/,API 服务自动挂载)
```

### 构建

```bash
cd web && npm run build
```

构建产物 `web/dist/` 会被 API 服务自动挂载到根路径，生产部署只需启动 `uvicorn` 即可。

---

## 测试

### 运行测试

```bash
# 全量测试
python -m pytest tests/ -q

# 仅治理面测试(P-B1~P-B8)
python -m pytest tests/storage/test_governance_pb.py -v

# 仅 API 测试
python -m pytest tests/api/ -v

# 仅集成测试
python -m pytest tests/integration/ -v
```

### 测试组织

| 目录 | 覆盖范围 |
|---|---|
| `tests/api/` | API 路由、SSE、审批、鉴权、并发 |
| `tests/domain/` | Team/Agent/Worker/Library 领域模型 |
| `tests/runtime/` | TeamCompiler、nodes、approval、evolution、skills |
| `tests/storage/` | 所有 Repo + 迁移框架 + 治理面 |
| `tests/integration/` | 端到端：多级团队、跨级审批、MCP 集成、preset 安装 |
| `tests/models/` | ModelProvider + 各 adapter |
| `tests/tools/` | ToolRegistry + 内置 skill + MCP 工具 |
| `tests/security/` | AES-GCM 加密 |
| `tests/presets/` | 预置团队 catalog + 安装 |

### 测试规模

- **797+ 测试用例**，覆盖所有模块
- 含端到端集成测试（多级团队 + MCP + 审批全流程）
- 含并发安全测试（library check-then-set、SSE 断线重连）
- 含治理面专项测试（RBAC / PEP / Skill 供应链 / 配额告警 / 多维统计）

---

## 模块索引

```
agentteam/
├── api/                      # FastAPI 后端
│   ├── server.py             # app 工厂 create_app
│   ├── auth.py               # AuthMiddleware + require_permission(RBAC)
│   ├── deps.py               # 全局依赖容器(解 auth↔server 循环)
│   ├── run_manager.py        # 后台线程执行 + interrupt/resume + WAT 双身份
│   ├── store.py              # TeamStore 内存注册表
│   ├── events.py             # EventBus + BroadcastTraceWriter
│   ├── webhook.py            # IM webhook 通知(钉钉/飞书/企微)
│   └── routes/
│       ├── teams.py          # 团队 CRUD
│       ├── runs.py           # 任务执行 + SSE + 审批
│       ├── library.py        # Agent 库 CRUD
│       ├── skills.py         # Skill 查询
│       ├── admin.py          # 管理面(PEP/Quota/Skill/audit)
│       ├── dashboard.py      # 仪表盘(基础 + 多维)
│       └── evolution.py      # 自进化历史 + 回滚
├── domain/                   # 领域模型
│   ├── team.py               # Team 容器(支持 root 树 / legacy leader+workers)
│   ├── agent.py              # Agent 节点(支持 ref / children / skills)
│   ├── worker.py             # Worker 兼容层
│   ├── library.py            # AgentLibrary($ref 复用 + 循环检测)
│   ├── approval.py           # ApprovalPolicy(step/worker/tool 三级)
│   ├── mcp_server.py         # MCPServer(含 5 种鉴权)
│   └── serializer.py         # Team ↔ JSON 双向转换
├── runtime/                  # 执行内核
│   ├── graph.py              # TeamCompiler(Team → StateGraph,含 DAG 条件求值)
│   ├── nodes.py              # leader_plan / worker ReAct / leader_review + PEP 拦截
│   ├── state.py              # TeamState / WorkerState schema
│   ├── approval.py           # 审批门节点(interrupt 实现)
│   ├── skills.py             # SkillLoader(扫描 .md + 缓存 + reload)
│   ├── trace.py              # TraceWriter(三链 + per-run trace_id)
│   ├── pep.py                # PEPRepo + check_pep(零信任拦截)
│   ├── evolution.py          # EvolutionEngine(prompt/param 优化)
│   └── errors.py             # RunCancelledError 等
├── storage/                  # SQLite 持久化
│   ├── db.py                 # init_db + 迁移框架(v1~v7,幂等)
│   ├── base.py               # BaseSqliteRepo(共享 conn + lock)
│   ├── runs.py               # runs 表 + sum_tokens_by_team
│   ├── audit.py              # run_events(三链 + 聚合)
│   ├── admin_audit.py        # admin_events(时间范围检索)
│   ├── quotas.py             # quotas(三级状态)
│   ├── users.py              # users/roles/permissions(RBAC)
│   ├── skills_meta.py        # skills/skill_acls(供应链)
│   ├── teams.py              # teams 表
│   ├── library.py            # library_agents 表
│   └── evolution.py          # evolution_history 表
├── models/                   # 模型供应商抽象
│   ├── provider.py           # ModelProvider + ModelRef
│   └── adapters/             # qwen / openai / anthropic / ollama
├── tools/                    # 工具系统
│   ├── registry.py           # ToolRegistry(带缓存)
│   ├── mcp.py                # MCP 工具加载
│   └── skills/               # 内置 skill(read_file / search_web / ...)
├── presets/                  # 预置团队模板
├── security/
│   └── crypto.py             # AES-GCM 凭证加密
├── config.py                 # pydantic-settings 集中配置
└── cli.py                    # CLI 入口
```

---

## 里程碑

- [x] **M1** 基础设施层（SQLite + 迁移框架 + 配置 + 日志）
- [x] **M2** 领域与编译（Team / Worker / TeamCompiler / LangGraph）
- [x] **M3** 审批与轨迹（step / worker / tool 三级审批 + TraceWriter）
- [x] **M4** MCP 集成（子图 ReAct + 工具级审批 + MCP 工具加载）
- [x] **M5a** API（FastAPI + SSE + RunManager + interrupt/resume）
- [x] **M5b** Web UI（React + antd + SSE 实时控制台）
- [x] **M6** 示例团队 + 集成测试
- [x] **M7** 自进化引擎（prompt / param 优化 + 历史回滚）
- [x] **P-A** 治理基础（凭证安全 / API Key 鉴权 / 管理审计 / 配额 / Webhook）
- [x] **P-B** 治理面对标阿里云 AgentTeams
  - [x] P-B1 用户/角色/权限 + RBAC
  - [x] P-B2 WAT 双身份
  - [x] P-B3 Trace 三链结构
  - [x] P-B4 PEP 零信任指令级拦截
  - [x] P-B5 Skill 供应链安全
  - [x] P-B6 MCP Server 鉴权
  - [x] P-B7 配额告警阈值
  - [x] P-B8 仪表盘多维统计 + 审计时间范围检索

---

## License

MIT
