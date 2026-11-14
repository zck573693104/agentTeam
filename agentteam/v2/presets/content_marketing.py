"""内容营销工人池 — v2 worker-pool 预设(由 v1 Agent 树压平:Router + 扁平工人池)。

用法: 逐个 POST /api/v2/workers 注册 WORKERS;MCP 服务随 worker 挂载
(session 启动时注册到 ToolRegistry,工具名 mcp:{name}:*)。
"""
from __future__ import annotations

from agentteam.v2.domain.worker import WorkerDef

WORKERS: list[WorkerDef] = [
    WorkerDef(
        name="planner",
        system_prompt="你是选题策划,使用 search MCP 搜索热点(query)与趋势(trends),产出选题清单。",
        tools=['mcp:search:query', 'mcp:search:trends'],
        mcp_servers=[{"name": "search", "command": "npx", "args": ["-y", "demo-mcp-search"], "transport": "stdio"}],
    ),
    WorkerDef(
        name="writer",
        system_prompt="你是写手,使用 read_file/write_file 撰写文章,完成后一句话汇报。",
    ),
    WorkerDef(
        name="seo",
        system_prompt="你是 SEO 优化师,使用 search MCP 查关键词(keywords),给出优化建议。",
        tools=['mcp:search:keywords'],
        mcp_servers=[{"name": "search", "command": "npx", "args": ["-y", "demo-mcp-search"], "transport": "stdio"}],
    ),
]

METADATA = {
    "name": "content_marketing",
    "title": "内容营销工人池",
    "description": "选题策划/写手/SEO 优化;search MCP",
}
