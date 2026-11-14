"""个人助理工人池 — v2 worker-pool 预设(由 v1 Agent 树压平:Router + 扁平工人池)。

用法: 逐个 POST /api/workers 注册 WORKERS;MCP 服务随 worker 挂载
(session 启动时注册到 ToolRegistry,工具名 mcp:{name}:*)。
"""
from __future__ import annotations

from agentteam.v2.domain.worker import WorkerDef

WORKERS: list[WorkerDef] = [
    WorkerDef(
        name="info_collector",
        system_prompt="你是信息收集员,使用 search MCP 搜索信息(query)与趋势(trends),汇总成简报。",
        tools=['mcp:search:query', 'mcp:search:trends'],
        mcp_servers=[{"name": "search", "command": "npx", "args": ["-y", "demo-mcp-search"], "transport": "stdio"}],
    ),
    WorkerDef(
        name="schedule_coordinator",
        system_prompt="你是日程协调员,使用 calendar MCP 查询空闲时段(find_slots)与创建日程(create)。",
        tools=['mcp:calendar:find_slots', 'mcp:calendar:create'],
        mcp_servers=[{"name": "calendar", "command": "npx", "args": ["-y", "demo-mcp-calendar"], "transport": "stdio"}],
    ),
    WorkerDef(
        name="notifier",
        system_prompt="你是提醒通知员,使用 email MCP 发送邮件(send_email)。发送前需人工审批。",
        tools=['mcp:email:send_email'],
        approval_targets=['mcp:email:send_email'],
        mcp_servers=[{"name": "email", "command": "npx", "args": ["-y", "demo-mcp-email"], "transport": "stdio"}],
    ),
]

METADATA = {
    "name": "personal_assistant",
    "title": "个人助理工人池",
    "description": "信息/日程/提醒;search/calendar/email MCP,发信审批",
}
