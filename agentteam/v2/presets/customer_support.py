"""客服工人池 — v2 worker-pool 预设(由 v1 Agent 树压平:Router + 扁平工人池)。

用法: 逐个 POST /api/v2/workers 注册 WORKERS;MCP 服务随 worker 挂载
(session 启动时注册到 ToolRegistry,工具名 mcp:{name}:*)。
"""
from __future__ import annotations

from agentteam.v2.domain.worker import WorkerDef

WORKERS: list[WorkerDef] = [
    WorkerDef(
        name="frontline",
        system_prompt="你是一线客服,使用 ticket MCP 列出/查看工单(list/get)与创建内部备注(note)。",
        tools=['mcp:ticket:list', 'mcp:ticket:get', 'mcp:ticket:note'],
        mcp_servers=[{"name": "ticket", "command": "npx", "args": ["-y", "demo-mcp-ticket"], "transport": "stdio"}],
    ),
    WorkerDef(
        name="specialist",
        system_prompt="你是技术专家,使用 ticket MCP 解决工单(resolve),必要时 read_file 查看文档。",
        tools=['mcp:ticket:resolve_ticket', 'read_file'],
        max_iterations=8,
        mcp_servers=[{"name": "ticket", "command": "npx", "args": ["-y", "demo-mcp-ticket"], "transport": "stdio"}],
    ),
    WorkerDef(
        name="complaint_handler",
        system_prompt="你是投诉处理员,使用 ticket MCP 升级投诉工单。升级前需人工审批。",
        tools=['mcp:ticket:escalate_complaint'],
        approval_targets=['mcp:ticket:escalate_complaint'],
        mcp_servers=[{"name": "ticket", "command": "npx", "args": ["-y", "demo-mcp-ticket"], "transport": "stdio"}],
    ),
]

METADATA = {
    "name": "customer_support",
    "title": "客服工人池",
    "description": "一线/专家/投诉处理;ticket MCP,升级审批",
}
