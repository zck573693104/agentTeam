"""研发小队示例团队定义。

5 角色:Leader(技术主管) + 需求分析员 + 代码工程师 + 测试员 + Reviewer。
验证 Leader-Worker 编排、ReAct 工具循环、MCP+原生工具混用、声明式审批。

用法:
    from examples.dev_team import DEV_TEAM
    # 或通过 CLI: agentteam register-dev-team
"""
from __future__ import annotations

DEV_TEAM: dict = {
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
