# AgentTeam

本地多智能体协作框架（迷你 AgentTeams），基于 Python + LangGraph。

## 安装

```bash
pip install -e ".[qwen,dev]"
```

## 模块

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
- `agentteam.harness` —— **SP8：opencode 套壳执行引擎**（Agent = 控制平面 + opencode 底座）
  - `opencode_client.py` — opencode v2 `/api/*` REST + SSE 客户端（自动重连、回合锚点）
  - `translator.py` — Team JSON → opencode v2 会话/prompt 内联/计划 schema/MCP/provider 配置
  - `approval.py` — 三级审批桥（step/worker 控制平面门 + tool 级事后中断与审计）
  - `engine.py` — HarnessRunner（plan→dispatch(seq/dag)→review，graph 协议适配 + 快照续跑）
  - `events.py` — opencode 事件 → AgentTeam trace 事件词表（前端零改动）
  - `runner.py` — 状态持久化（run_engine_state 表）+ 引擎工厂
- `agentteam.api` —— FastAPI 后端 API（团队注册、任务提交、SSE 实时推送、审批续跑、用量统计）
  - `server.py` — FastAPI app 工厂（create_app）
  - `serializer.py` — Team JSON ↔ dataclass 转换
  - `store.py` — TeamStore 内存注册表
  - `events.py` — EventBus + BroadcastTraceWriter
  - `run_manager.py` — 后台线程执行 + interrupt/resume
  - `routes/` — teams / runs / dashboard 路由

## 快速示例

```python
from agentteam.models import provider
from agentteam.tools.registry import ToolRegistry
from agentteam.tools.skills import register_builtin_skills
from agentteam.storage.db import init_db
from agentteam.storage.runs import RunRepo

# 模型
llm = provider.ModelProvider().get_llm(provider.ModelRef("qwen", "qwen-max"))

# 工具
reg = ToolRegistry()
register_builtin_skills(reg)
print(reg.list_names())  # ['read_file', 'write_file', 'list_dir', 'search_web']

# 存储
conn = init_db("data/agentteam.db")
run_id = RunRepo(conn).create_run("dev_team", "示例任务")
```

## 研发小队示例

内置「研发小队」团队验证框架完整流程:Leader 拆解 → 需求分析 → 编码 → 测试 → 审查。

### 1. 启动 API 服务

```bash
pip install -e ".[qwen,dev]"
uvicorn agentteam.api.server:create_app --factory
```

### 2. 注册研发小队

研发小队定义在 `examples/dev_team.py`（`DEV_TEAM` 字典）。通过 CLI 注册（需 `pip install -e ".[qwen,dev]"`）：

```bash
agentteam register-dev-team
```

CLI 会读取 `DEV_TEAM` 并 POST 到 `http://localhost:8000/api/teams`。

### 3. 提交任务

```bash
curl -X POST http://localhost:8000/api/runs \
  -H "Content-Type: application/json" \
  -d '{"team_name": "dev_team", "task": "实现一个 hello world 程序"}'
```

### 4. 查看实时轨迹

- **Web UI**: 浏览器打开 http://localhost:8000
- **SSE**: `GET http://localhost:8000/api/runs/{run_id}/stream`

### 5. 审批续跑

当 Leader step 级或 Worker tool 级审批触发时,run 状态变为 `interrupted`:

```bash
curl -X POST http://localhost:8000/api/runs/{run_id}/approve \
  -H "Content-Type: application/json" \
  -d '{"approved": true, "reason": "同意"}'
```

## API 服务配置

```bash
pip install -e ".[qwen,dev]"
uvicorn agentteam.api.server:create_app --factory
```

API 端点：
- `GET/POST /api/teams` — 团队管理
- `POST /api/runs` — 提交任务
- `GET /api/runs/{id}/stream` — SSE 实时事件流
- `POST /api/runs/{id}/approve` — 审批续跑
- `GET /api/dashboard` — 用量统计

## 状态

- [x] M1 基础设施层
- [x] M2 领域与编译（Team/Worker/TeamCompiler/LangGraph）
- [x] M3 审批与轨迹
- [x] M4 MCP 集成（子图 ReAct + 工具级审批 + MCP 工具加载）
- [x] M5a API（FastAPI + SSE + RunManager）
- [x] M5b Web UI（React + antd + SSE 实时控制台）
- [x] M6 示例团队 + 测试
- [x] SP7 Skill 系统 + 自进化
- [x] SP8 opencode 套壳引擎（双引擎：`Team.engine = "langgraph" | "opencode"`）

## SP8：opencode 套壳引擎（双引擎架构）

AgentTeam 现在支持把执行层架在开源 [opencode](https://github.com/sst/opencode)（MIT）上：
AgentTeam 保留**控制平面**（Team schema、三级审批策略、专家库、SP7 自进化、审计、
Web 控制台），opencode server 承担 **agent loop / 工具系统 / 模型接入 / MCP**。
设计详见 [docs/opencode-harness-design.md](docs/opencode-harness-design.md)。

### 1. 启动 opencode server（执行底座）

```bash
npm i -g opencode-ai        # 或 curl -fsSL https://opencode.ai/install | bash
opencode serve --port 4117  # 在你的项目目录下运行（worker 的文件操作落在该目录）
```

### 2. 启动 AgentTeam API（控制平面）

```bash
uvicorn agentteam.api.server:create_app --factory
```

### 3. 注册 opencode 引擎团队并提交任务

```bash
curl -X POST http://localhost:8000/api/teams -H "Content-Type: application/json" -d '{
  "name": "oc_dev", "description": "套壳研发小队", "engine": "opencode",
  "root": {
    "name": "leader", "role": "supervisor", "system_prompt": "你是研发主管",
    "children": [
      {"name": "coder",  "role": "worker", "system_prompt": "你负责写代码", "tools": ["write_file", "bash"]},
      {"name": "tester", "role": "worker", "system_prompt": "你负责测试", "tools": ["read_file", "bash"]}
    ]},
  "default_model": {"provider": "qwen", "name": "qwen-max"},
  "skills": [], "mcp_servers": []
}'

curl -X POST http://localhost:8000/api/runs \
  -H "Content-Type: application/json" \
  -d '{"team_name": "oc_dev", "task": "实现一个 hello world 程序"}'
```

不声明 `engine` 的团队自动走原 LangGraph 引擎，行为完全不变。

### 4. 配置（环境变量）

| 变量 | 含义 | 默认 |
|---|---|---|
| `AGENTTEAM_OPENCODE_URL` | opencode server 地址 | `http://127.0.0.1:4096` |
| `AGENTTEAM_OC_MODEL` | 默认模型 `provider/model` | `opencode/ling-3.0-flash-fin-free`（免费） |
| `OPENCODE_SERVER_PASSWORD` | opencode server Basic Auth | 无 |
| `AGENTTEAM_DEFAULT_ENGINE` | Team 未声明 engine 时的默认引擎 | `langgraph` |
| `AGENTTEAM_OC_TIMEOUT` | 单次 prompt 等待上限（秒） | `600` |
| `AGENTTEAM_OC_TOOL_GUARD` | 白名单外工具调用的处置：`strict`（中断+审批）/ `audit`（只记账继续跑）/ `off` | `strict` |
| `AGENTTEAM_SKILLS_DIR` | SP7 技能目录（`uvicorn --factory` 无法传参时的唯一入口） | 不设＝不加载技能 |
| `AGENTTEAM_OC_SMOKE=0` | 关闭真实 opencode 冒烟测试 | 自动探测 |
| `AGENTTEAM_HARNESS_DISABLED=1` | 完全禁用 harness 引擎 | 启用 |

### 5. 测试

```bash
python -m pytest tests -q          # 全量（新旧引擎 + 734 用例）
python -m pytest tests/harness -q  # 仅套壳引擎（fake server，无外部依赖）
# 真实 opencode 冒烟：先 `opencode serve --port 4117`，再跑 tests/harness/test_real_opencode.py
```

## 容器部署（内网一键起）

仓库自带两个镜像定义与编排：`Dockerfile`（控制平面，React 控制台在镜像内构建后
由 FastAPI 同源挂载）与 `docker/opencode.Dockerfile`（执行底座，版本钉死）。

```bash
cp .env.example .env               # 至少改 OPENCODE_SERVER_PASSWORD、AGENTTEAM_OC_MODEL
docker compose build               # opencode 版本可用 --build-arg OPENCODE_VERSION=1.18.32
docker compose up -d
docker compose logs -f agentteam   # 控制台 http://127.0.0.1:8000（只绑宿主 loopback）
```

拓扑与安全边界：

- **opencode 只监听容器 loopback**：它与控制平面共享 network namespace
  （`network_mode: "service:agentteam"`），因此宿主和同网段机器都连不上 4117，
  而 `AGENTTEAM_OPENCODE_URL=http://127.0.0.1:4117` 天然可用。宿主 `8000` 也只绑
  `127.0.0.1`，对外访问交给内网反向代理或 SSH 隧道。
- **两个非 root 用户**：控制平面 `agentteam`(uid 10001)、底座 `opencode`(uid 10001)，
  worker 的 `write_file` / `bash` 都在 opencode 容器的 `/workspace` 内发生。
- **代码不出域**：把目标仓库挂成 `AGENTTEAM_WORKSPACE`（默认 `./workspace`）。
- **模型不出域**：provider 指向内网网关（vLLM / Ollama / 自托管 DeepSeek / LiteLLM），
  密钥经环境变量透传，opencode 配置里用 `{env:DEEPSEEK_API_KEY}` 引用，
  不要把明文写进 `opencode.jsonc` 或 Team JSON。
- **持久化**：`agentteam-data`（SQLite：团队、run 快照、审批、审计、进化历史）、
  `opencode-config` / `opencode-data`（opencode 自身配置与会话库）都是命名卷。
- **升级底座版本**：改 `OPENCODE_VERSION` 前先在目标版本上跑
  `tests/harness/test_real_opencode.py`；引擎的版本兼容门会对主版本偏离返回 502
  并给出降级指引，小版本偏离记 `backend_warning` 事件。

首次起完注册团队：

```bash
agentteam install-preset enterprise_dev --engine opencode   # 混跑：预设走套壳引擎
# 或按上面「3. 注册 opencode 引擎团队」的 curl 自建
```

## 企业部署指引（opencode 路线）

1. **数据不出域**：AgentTeam 控制平面 + opencode server 全部内网部署；模型走
   内网网关（vLLM/Ollama/DeepSeek 自托管，或 LiteLLM 统一代理），Team JSON 里
   `default_model` 指向网关 provider 即可。
2. **版本钉住（重要）**：引擎契约只对 opencode **1.18.x（已验证 1.18.32）** 做
   功能保证——上游 HTTP 面按版本漂移（v2 渠道 experimental 无兼容承诺，`tools`/
   `deny`/`wait` 的行为随版本变化，issue 对照见设计文档 §9）。run 提交时引擎会
   自动做版本兼容门：主版本偏离直接拒绝（502，附降级指引），小版本偏离告警并
   记入 run 审计（`backend_warning` 事件）。升级 opencode 前先重跑
   `tests/harness/test_real_opencode.py`。
3. **权限最小化**：Worker 的 `tools` 白名单 + `approval_policy(level="tool", targets=[...])`
   把写文件/执行命令纳入人工审批。**注意 v2 底座上的语义是「事后中断 + 审计」**
   （opencode v2 无会话级权限规则集，见设计文档 §4/§8）：违规调用一旦被观测到即
   中断回合、写 `tool_denied` 并挂起等人工，放行后从断点续跑；但**首个被观测到的
   调用可能已经执行完毕**，需要「执行前拦住」语义的合规场景请把该 worker 的
   `engine` 留在 `langgraph`。生产建议给 opencode server 设置
   `OPENCODE_SERVER_PASSWORD`、用防火墙限制 127.0.0.1，并把 server 跑在
   容器/独立用户下（worker 的文件操作与命令都在其工作目录内发生）——
   上面「容器部署」那套编排就是把这几条钉死的默认形态。
4. **审计与合规**：所有审批决策、工具调用、token 消耗落在 SQLite（`run_events` /
   `approvals` / `evolution_history`），可对接企业日志管道；Web 控制台实时查看。
5. **成本控制**：`GET /api/dashboard` 按团队/状态聚合 token 用量；模型侧用
   LiteLLM 配预算与限流。
6. **高可用**：审批等待期间 run 快照持久化（`run_engine_state` 表），服务重启后
   审批仍可续跑；进化引擎失败自动隔离，不影响主流程。
7. **已知边界**：执行底座已迁到 opencode v2 `/api/*` 面（v1.18.32 实测）。v2 不提供
   per-request `system`/`format`/`tools`，也没有会话级 permission 规则集，因此
   system prompt 内联进用户文本、Plan JSON 靠 prompt + 宽容提取、工具管控靠
   「prompt 约束 + 事后中断审计」（逐项对策见设计文档 §8）。若 opencode 后续补齐
   事前权限门，只需替换 `approval.review_tool_call` 的处置分支，控制平面与审计词表不变。
8. **预设舰队混跑**：企业预设默认 langgraph 引擎；`agentteam install-preset
   enterprise_dev --engine opencode` 可把预设（含依赖 sub-team）安装为 opencode
   引擎，与 langgraph 团队同 fleet 混跑，`AGENTTEAM_DEFAULT_ENGINE` 可设全站默认。
   适配器扩展（DeepSeek Harness / ZCode）评估结论与触发信号见设计文档 §10：
   当前均不建议投入，模型侧需求经 LiteLLM 网关解决。
