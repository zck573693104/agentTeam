from __future__ import annotations

from langchain_core.tools import BaseTool

from agentteam.domain.mcp_server import MCPServer


class ToolRegistry:
    """工具统一注册表。Worker 配置里按名字引用工具，运行时取出绑定到 LLM。"""

    def __init__(self, mcp_loader=None) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._mcp_loader = mcp_loader  # None 时用 default_mcp_loader
        # BUG-06:缓存已成功加载的 MCP server.name,避免 loader 重复调用。
        # default_mcp_loader 会 spawn npx 子进程,而 TeamCompiler.compile()
        # 每次 create_run 都会触发 register_mcp_tools,无缓存时 n×m 次 run
        # 会泄漏 n×m 个子进程。loader 调用成功后才加入此集合;失败不加入
        # (允许重试)。
        self._loaded_servers: set[str] = set()

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> bool:
        """注销已注册的工具。返回是否成功移除。"""
        return self._tools.pop(name, None) is not None

    def register_mcp_tools(self, server: MCPServer) -> list[str]:
        """加载 MCP 工具并注册，加 mcp:{server.name}: 前缀防冲突。

        幂等：已注册的同名工具会被跳过。TeamCompiler.compile() 每次 run
        都会调用此方法，共享 registry 不能因重复注册而报错。

        BUG-06:loader 调用本身不幂等(default_mcp_loader 会 spawn npx
        子进程),仅靠工具名检查无法避免子进程泄漏。此处用 _loaded_servers
        缓存 server.name,二次调用直接跳过 loader,返回已注册的工具名。

        已知限制(BUG-12,本次不深入修复):default_mcp_loader 内部用
        asyncio.run 创建短命 event loop,loop 关闭后返回的 BaseTool 持有
        的 MCP client 可能已断连。由于本缓存使 loader 只调用一次,且工具
        invoke 在 LangGraph worker 线程内执行,短命 loop 问题已缓解。
        彻底修复需重写 MCP client 生命周期管理,超出当前范围。
        """
        from agentteam.tools.mcp import default_mcp_loader

        # BUG-06:已加载的 server 直接跳过 loader 调用,避免重复 spawn 子进程。
        # 返回已注册的工具名(保持幂等语义,调用方仍能拿到工具列表)。
        if server.name in self._loaded_servers:
            prefix = f"mcp:{server.name}:"
            return [name for name in self._tools if name.startswith(prefix)]

        loader = self._mcp_loader or default_mcp_loader
        tools = loader(server)
        # 仅在 loader 调用成功(未抛异常)后才缓存,失败时不加入 → 允许重试
        self._loaded_servers.add(server.name)
        registered = []
        for tool in tools:
            tool.name = f"mcp:{server.name}:{tool.name}"
            if tool.name in self._tools:
                # 二级防护:已注册则跳过,不覆盖已有工具
                registered.append(tool.name)
                continue
            self._tools[tool.name] = tool
            registered.append(tool.name)
        return registered

    def get_tools(self, names: list[str]) -> list[BaseTool]:
        missing = [n for n in names if n not in self._tools]
        if missing:
            raise KeyError(f"Tools not found: {missing}")
        return [self._tools[n] for n in names]

    def list_names(self) -> list[str]:
        return list(self._tools.keys())
