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
print(reg.list_names())  # ['read_file', 'write_file', 'list_dir']

# 存储
conn = init_db("data/agentteam.db")
run_id = RunRepo(conn).create_run("dev_team", "示例任务")
```

## 启动 API 服务

```bash
pip install -e ".[dev]"
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
- [ ] M6 示例团队 + 测试
