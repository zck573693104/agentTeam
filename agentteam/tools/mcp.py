from __future__ import annotations

from langchain_core.tools import BaseTool

from agentteam.domain.mcp_server import MCPServer


def default_mcp_loader(server: MCPServer) -> list[BaseTool]:
    """用 langchain-mcp-adapters 的 MultiServerMCPClient 加载 MCP 工具。

    lazy import：仅在实际调用时引入 langchain-mcp-adapters，
    测试中通过注入 fake loader 避免安装此依赖。
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient

    if server.transport == "http":
        server_config = {
            server.name: {"url": server.url, "transport": "http"}
        }
    else:
        server_config = {
            server.name: {
                "command": server.command,
                "args": server.args,
                "env": server.env,
                "transport": "stdio",
            }
        }

    import asyncio

    client = MultiServerMCPClient(server_config)
    return asyncio.run(client.get_tools())
