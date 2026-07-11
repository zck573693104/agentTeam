# AgentTeam

本地多智能体协作框架（迷你 AgentTeams），基于 Python + LangGraph。

## 安装

```bash
pip install -e ".[qwen,dev]"
```

## 模块

- `agentteam.models` —— 多供应商模型抽象（Qwen/OpenAI/Anthropic/Ollama）
- `agentteam.tools` —— ToolRegistry + 原生技能（read_file/write_file/list_dir）
- `agentteam.storage` —— SQLite 持久化（runs / run_events / approvals）

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

## 状态

- [x] M1 基础设施层
- [ ] M2 领域与编译（Team/Worker/TeamCompiler/LangGraph）
- [ ] M3 审批与轨迹
- [ ] M4 MCP 集成
- [ ] M5 API + Web UI
- [ ] M6 示例团队 + 测试
