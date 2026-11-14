"""日程管理工人池 — v2 worker-pool 预设(由 v1 Agent 树压平:Router + 扁平工人池)。

用法: 逐个 POST /api/workers 注册 WORKERS;MCP 服务随 worker 挂载
(session 启动时注册到 ToolRegistry,工具名 mcp:{name}:*)。
"""
from __future__ import annotations

from agentteam.v2.domain.worker import WorkerDef

WORKERS: list[WorkerDef] = [
    WorkerDef(
        name="planner",
        system_prompt="你是日程规划师,使用 calendar MCP 查询空闲时段(find_slots)与安排日程(create)。",
        tools=['mcp:calendar:find_slots', 'mcp:calendar:create'],
        mcp_servers=[{"name": "calendar", "command": "npx", "args": ["-y", "demo-mcp-calendar"], "transport": "stdio"}],
    ),
    WorkerDef(
        name="reminder",
        system_prompt="你是提醒专员,使用 calendar MCP 设置提醒(set_reminder)。",
        tools=['mcp:calendar:set_reminder'],
        mcp_servers=[{"name": "calendar", "command": "npx", "args": ["-y", "demo-mcp-calendar"], "transport": "stdio"}],
    ),
    WorkerDef(
        name="prioritizer",
        system_prompt="你是优先级评估师,使用 read_file 查看任务清单,排序并说明理由。",
        tools=['read_file'],
    ),
]

METADATA = {
    "name": "schedule_management",
    "title": "日程管理工人池",
    "description": "规划/提醒/优先级;calendar MCP",
}
