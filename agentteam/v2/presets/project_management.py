"""项目管理工人池 — v2 worker-pool 预设(由 v1 Agent 树压平:Router + 扁平工人池)。

用法: 逐个 POST /api/workers 注册 WORKERS;MCP 服务随 worker 挂载
(session 启动时注册到 ToolRegistry,工具名 mcp:{name}:*)。
"""
from __future__ import annotations

from agentteam.v2.domain.worker import WorkerDef

WORKERS: list[WorkerDef] = [
    WorkerDef(
        name="task_breaker",
        system_prompt="你是任务拆解师,使用 task MCP 创建任务(create_task)。",
        tools=['mcp:task:create_task'],
        mcp_servers=[{"name": "task", "command": "npx", "args": ["-y", "demo-mcp-task"], "transport": "stdio"}],
    ),
    WorkerDef(
        name="progress_tracker",
        system_prompt="你是进度跟踪师,使用 task MCP 查询任务状态(list_tasks)。",
        tools=['mcp:task:list_tasks'],
        mcp_servers=[{"name": "task", "command": "npx", "args": ["-y", "demo-mcp-task"], "transport": "stdio"}],
    ),
    WorkerDef(
        name="risk_assessor",
        system_prompt="你是风险评估师,使用 read_file 查看风险登记册与项目文档,输出风险清单。",
        tools=['read_file'],
    ),
]

METADATA = {
    "name": "project_management",
    "title": "项目管理工人池",
    "description": "拆解/跟踪/风险评估;task MCP",
}
