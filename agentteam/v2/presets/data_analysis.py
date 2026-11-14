"""数据分析工人池 — v2 worker-pool 预设(由 v1 Agent 树压平:Router + 扁平工人池)。

用法: 逐个 POST /api/workers 注册 WORKERS;MCP 服务随 worker 挂载
(session 启动时注册到 ToolRegistry,工具名 mcp:{name}:*)。
"""
from __future__ import annotations

from agentteam.v2.domain.worker import WorkerDef

WORKERS: list[WorkerDef] = [
    WorkerDef(
        name="sql_engineer",
        system_prompt="你是 SQL 工程师,使用 db MCP 执行查询(query)与查看 schema,必要时 read_file 查看数据字典。",
        tools=['mcp:db:query', 'mcp:db:schema', 'read_file'],
        mcp_servers=[{"name": "db", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-postgres", "--connection-string", "postgresql://localhost/analytics"], "transport": "stdio"}],
    ),
    WorkerDef(
        name="visualizer",
        system_prompt="你是可视化师,使用 chart MCP 生成图表(chart),产出分析结论。",
        tools=['mcp:chart:chart'],
        mcp_servers=[{"name": "chart", "command": "npx", "args": ["-y", "demo-mcp-chart"], "transport": "stdio"}],
    ),
]

METADATA = {
    "name": "data_analysis",
    "title": "数据分析工人池",
    "description": "SQL 工程师/可视化师;db+chart MCP",
}
