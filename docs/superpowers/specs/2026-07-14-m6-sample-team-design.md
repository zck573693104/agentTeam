# M6 示例团队 + 集成测试 设计文档

## 1. 目标

为 AgentTeam 框架内置一个开箱即用的「研发小队」示例团队,验证 Leader-Worker 编排、ReAct 工具循环、MCP+原生工具混用、声明式审批、轨迹记录与用量统计。配套全面集成测试套件,确保端到端流程可靠。

## 2. 范围

| 交付物 | 文件 | 职责 |
|--------|------|------|
| search_web stub 技能 | `agentteam/tools/skills/search_web.py` | 模拟网络搜索,返回基于查询的 mock 结果 |
| CLI 入口 | `agentteam/cli.py` | `register-dev-team` 命令注册研发小队 |
| 示例团队定义 | `examples/dev_team.py` | 研发小队 5 角色团队 JSON 定义 |
| 集成测试套件 | `tests/integration/` | E2E + 场景 + 单元 + 配置验证测试 |
| 文档更新 | `README.md` | M6 标记完成 + 使用示例章节 |

## 3. search_web stub 技能

### 3.1 设计

与 `read_file`/`write_file`/`list_dir` 同级的原生 `@tool` 技能,注册到 `register_builtin_skills`。

```python
@tool
def search_web(query: str, max_results: int = 3) -> str:
    """搜索网络,返回相关结果摘要。"""
```

### 3.2 行为

- 输入:`query`(搜索关键词)、`max_results`(最大结果数,默认 3)
- 输出:模拟搜索结果文本,包含查询回显 + 生成的 mock 结果条目
- 不调用真实搜索 API,仅用于演示 Worker ReAct 工具循环
- 结果格式:每条结果包含标题、URL(mock)、摘要文本

### 3.3 注册

在 `agentteam/tools/skills/__init__.py` 的 `_BUILTIN_TOOLS` 列表中追加 `search_web`。

## 4. 研发小队示例团队

### 4.1 角色定义

| 角色 | 职责 | 工具 | 审批 |
|------|------|------|------|
| Leader(技术主管) | 拆需求、派活、汇总 | — | step 级(可选) |
| 需求分析员 | 拆用户故事、定验收标准 | `search_web` | — |
| 代码工程师 | 写/改代码 | `read_file`、`write_file`、`mcp:git:git_status`、`mcp:git:git_diff`、`mcp:git:git_log` | `write_file` 前 tool 级审批 |
| 测试员 | 写测试用例 | `read_file`、`write_file` | `write_file` 前 tool 级审批 |
| Reviewer | 审查代码与测试 | `read_file` | — |

### 4.2 团队 JSON 结构

```python
DEV_TEAM = {
    "name": "dev_team",
    "description": "研发小队 — Leader + 需求分析 + 代码 + 测试 + 审查",
    "leader": {
        "name": "tech_lead",
        "role": "技术主管",
        "system_prompt": "你是技术主管,负责拆解需求、分配步骤、汇总产出。",
        "model": {"provider": "qwen", "name": "qwen-max", "temperature": 0.7, "streaming": True},
        "approval_policy": {"level": "step"},
    },
    "workers": [
        {
            "name": "analyst",
            "role": "需求分析员",
            "description": "拆用户故事、定验收标准",
            "system_prompt": "你是需求分析员,使用 search_web 搜索相关资料,拆解用户故事并定义验收标准。",
            "tools": ["search_web"],
            "max_iterations": 5,
        },
        {
            "name": "coder",
            "role": "代码工程师",
            "description": "写/改代码",
            "system_prompt": "你是代码工程师,使用 read_file/write_file 和 git 工具完成编码任务。写文件前需审批。",
            "tools": ["read_file", "write_file", "mcp:git:git_status", "mcp:git:git_diff", "mcp:git:git_log"],
            "approval_policy": {"level": "tool", "targets": ["write_file"]},
            "max_iterations": 10,
        },
        {
            "name": "tester",
            "role": "测试员",
            "description": "写测试用例",
            "system_prompt": "你是测试员,使用 read_file/write_file 编写测试用例。写文件前需审批。",
            "tools": ["read_file", "write_file"],
            "approval_policy": {"level": "tool", "targets": ["write_file"]},
            "max_iterations": 10,
        },
        {
            "name": "reviewer",
            "role": "Reviewer",
            "description": "审查代码与测试",
            "system_prompt": "你是代码审查员,使用 read_file 审查代码与测试质量,给出改进建议。",
            "tools": ["read_file"],
            "max_iterations": 5,
        },
    ],
    "default_model": {"provider": "qwen", "name": "qwen-max", "temperature": 0.7, "streaming": True},
    "skills": ["read_file", "write_file", "list_dir", "search_web"],
    "mcp_servers": [
        {
            "name": "git",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-git", "--repository", "."],
            "transport": "stdio",
        }
    ],
}
```

### 4.3 交付方式

`examples/dev_team.py` 导出 `DEV_TEAM` dict。CLI 和脚本共用此定义(DRY)。

## 5. CLI 入口

### 5.1 命令

```bash
# 注册研发小队到本地 API(默认 http://localhost:8000)
agentteam register-dev-team

# 指定 API 地址
agentteam register-dev-team --api http://localhost:9000
```

### 5.2 实现

- 用 `typer` 实现(轻量,类型安全)
- `register-dev-team` 命令:从 `examples/dev_team.py` 导入 `DEV_TEAM`,POST 到 `/api/teams`
- 成功输出团队名,失败输出错误信息
- `pyproject.toml` 添加 `typer` 依赖 + `[project.scripts]` 入口点

### 5.3 入口点配置

```toml
[project.scripts]
agentteam = "agentteam.cli:app"
```

## 6. 集成测试套件

### 6.1 测试矩阵

| 测试文件 | 场景 | 验证点 |
|----------|------|--------|
| `test_e2e_normal.py` | mock LLM 跑完整 run:Leader 拆解→Worker 执行→Leader 汇总→结束 | 状态流转 pending→running→completed,轨迹事件序列,token 统计 |
| `test_e2e_approval.py` | step 级审批中断→模拟人类决策→恢复→结束 | interrupt 触发,interrupted 状态,approve 后 resume,completed |
| `test_tool_approval.py` | write_file 触发 tool 级审批中断→恢复 | tool 级 interrupt,approval_requested 事件,resume 后继续 |
| `test_worker_retry.py` | Worker 执行失败→Leader 重试 | Leader 收到 worker_end(failed),重新分配或标记失败 |
| `test_search_web.py` | search_web stub 单元测试 | 返回结构正确,查询回显,max_results 生效 |
| `test_mcp_git_config.py` | MCP git 配置序列化/反序列化 | team_from_dict 正确解析 mcp_servers,MCPServer 字段正确 |
| `test_dev_team_script.py` | examples/dev_team.py 团队定义可解析 | DEV_TEAM dict → team_from_dict → Team dataclass 字段完整 |

### 6.2 测试策略

- 所有 E2E 测试使用 `FakeLLM` + `FakeModelProvider`(已有 conftest fixture)
- MCP 工具用 `fake_mcp_loader` 注入(不启动真实子进程)
- 审批测试用 `RunManager` 的 interrupt/resume 机制
- 每个测试独立使用 tmp_db,不共享状态

### 6.3 FakeLLM 响应编排

`FakeLLM` 按 `invoke_responses` 顺序返回。E2E 测试需精心编排响应序列:
1. Leader `with_structured_output` 返回 plan(steps)
2. Worker `invoke` 返回 ReAct 思考 + tool_call
3. Worker `invoke` 返回最终答案
4. Leader `with_structured_output` 返回 review(通过/重试)

## 7. 文档更新

### 7.1 README.md

- M6 状态标记 `[x]`
- 新增「快速示例:研发小队」章节:
  1. 启动 API 服务
  2. 注册研发小队(`agentteam register-dev-team`)
  3. 提交任务(`POST /api/runs`)
  4. 查看实时轨迹(Web UI 或 SSE)

### 7.2 不创建独立文档文件

所有文档整合到 README.md,避免文件膨胀。

## 8. 依赖变更

| 依赖 | 用途 | 安装方式 |
|------|------|----------|
| `typer` | CLI 框架 | `pip install -e ".[dev]"` 包含 |

`pyproject.toml` 的 `[project.optional-dependencies] dev` 中追加 `typer`。

## 9. 验证标准

- [ ] `search_web` 技能注册到 ToolRegistry,`list_names()` 包含 `search_web`
- [ ] `examples/dev_team.py` 的 `DEV_TEAM` 可被 `team_from_dict` 解析为 `Team` dataclass
- [ ] `agentteam register-dev-team` 命令成功注册团队到 API
- [ ] 所有集成测试通过(mock LLM,不依赖真实模型/MCP 子进程)
- [ ] 现有 170 个测试不受影响
- [ ] `npm run build` 仍通过(前端无变更)
- [ ] README.md 更新
