from __future__ import annotations

from langchain_core.tools import BaseTool

from agentteam.domain.mcp_server import MCPServer


class ToolRegistry:
    """工具统一注册表。Worker 配置里按名字引用工具，运行时取出绑定到 LLM。"""

    def __init__(self, mcp_loader=None) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._mcp_loader = mcp_loader  # None 时用 default_mcp_loader

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def register_mcp_tools(self, server: MCPServer) -> list[str]:
        """加载 MCP 工具并注册，加 mcp:{server.name}: 前缀防冲突。"""
        from agentteam.tools.mcp import default_mcp_loader

        loader = self._mcp_loader or default_mcp_loader
        tools = loader(server)
        registered = []
        for tool in tools:
            tool.name = f"mcp:{server.name}:{tool.name}"
            self.register(tool)
            registered.append(tool.name)
        return registered

    def get_tools(self, names: list[str]) -> list[BaseTool]:
        missing = [n for n in names if n not in self._tools]
        if missing:
            raise KeyError(f"Tools not found: {missing}")
        return [self._tools[n] for n in names]

    def list_names(self) -> list[str]:
        return list(self._tools.keys())
