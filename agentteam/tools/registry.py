from __future__ import annotations

from langchain_core.tools import BaseTool

from agentteam.domain.mcp_server import MCPServer
from agentteam.logging_config import get_logger

logger = get_logger("tools.registry")


def _server_cache_key(server: MCPServer) -> tuple:
    """生成 MCP server 的缓存 key。

    用 (name, command, args, transport, url, namespace) 唯一标识一个 MCP
    server 配置。同名但配置不同(command/args/transport/url/namespace 任一不同)
    的 server 视为不同实例,应各自独立触发 loader 调用,避免第二个 server
    被错误跳过导致工具漏注册(SP6-P2 / BUG-12 修复)。

    P3-4: namespace 加入 key,允许同名 server 用不同 namespace 各自独立注册。

    Args:
        server: MCP server 配置 dataclass。

    Returns:
        可哈希的 tuple,可作为 set 元素。args list 转 tuple 以保证可哈希。
    """
    return (
        server.name,
        server.command,
        tuple(server.args),
        server.transport,
        server.url,
        server.namespace,
    )


class ToolRegistry:
    """工具统一注册表。Worker 配置里按名字引用工具，运行时取出绑定到 LLM。"""

    def __init__(self, mcp_loader=None) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._mcp_loader = mcp_loader  # None 时用 default_mcp_loader
        # BUG-06:缓存已成功加载的 MCP server 配置,避免 loader 重复调用。
        # default_mcp_loader 会 spawn npx 子进程,而 TeamCompiler.compile()
        # 每次 create_run 都会触发 register_mcp_tools,无缓存时 n×m 次 run
        # 会泄漏 n×m 个子进程。loader 调用成功后才加入此集合;失败不加入
        # (允许重试)。
        # SP6-P2 / BUG-12 修正:用 (name, command, args, transport, url) tuple
        # 作 key,而非 server.name。同名但配置不同的 server 应各自独立缓存,
        # 否则第二个同名 server 会被错误跳过 loader 调用,工具不注册。
        # P3-4: namespace 加入 key;namespace=None 时回退到 name 作工具名前缀,
        # 用户可显式设 namespace 解冲突(原需改 server.name)。
        self._loaded_servers: set[tuple] = set()

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> bool:
        """注销已注册的工具。返回是否成功移除。"""
        return self._tools.pop(name, None) is not None

    def register_mcp_tools(self, server: MCPServer) -> list[str]:
        """加载 MCP 工具并注册,加 mcp:{namespace or name}: 前缀防冲突。

        幂等:已注册的同名工具会被跳过(并 logger.warning 提示用户)。
        TeamCompiler.compile() 每次 run 都会调用此方法,共享 registry
        不能因重复注册而报错。

        P3-4 改进:
        - 工具名前缀从硬编码 `mcp:{server.name}:` 改为 `server.tool_prefix`
          (即 `mcp:{namespace or name}:`),用户可设 MCPServer.namespace
          显式区分同名 server。
        - 冲突时由"静默跳过"改为 logger.warning,提醒用户工具被跳过 +
          建议设 namespace 解决。
        - _server_cache_key 加入 namespace 字段,允许同名不同 namespace
          的 server 各自独立缓存(避免第二个被错误跳过 loader)。

        BUG-06:loader 调用本身不幂等(default_mcp_loader 会 spawn npx
        子进程),仅靠工具名检查无法避免子进程泄漏。此处用 _loaded_servers
        缓存配置 tuple,二次调用直接跳过 loader,返回已注册的工具名。

        已知限制(BUG-12):default_mcp_loader 内部用 asyncio.run 创建
        短命 event loop,彻底修复需重写 MCP client 生命周期管理,超出当前范围。
        """
        from agentteam.tools.mcp import default_mcp_loader

        # BUG-06 / SP6-P2:已加载的 server 直接跳过 loader 调用,避免重复 spawn
        # 子进程。cache key 用完整配置 tuple(含 namespace),同名不同配置
        # 的 server 各自独立缓存(BUG-12 修复)。
        # 返回已注册的工具名(保持幂等语义,调用方仍能拿到工具列表)。
        key = _server_cache_key(server)
        if key in self._loaded_servers:
            prefix = server.tool_prefix
            return [name for name in self._tools if name.startswith(prefix)]

        loader = self._mcp_loader or default_mcp_loader
        tools = loader(server)
        # 仅在 loader 调用成功(未抛异常)后才缓存,失败时不加入 → 允许重试
        self._loaded_servers.add(key)
        registered = []
        prefix = server.tool_prefix
        for tool in tools:
            tool.name = f"{prefix}{tool.name}"
            if tool.name in self._tools:
                # P3-4:冲突由静默跳过改为 warning 日志,提示用户设 namespace
                logger.warning(
                    "MCP tool %s skipped: already registered "
                    "(set MCPServer.namespace to disambiguate same-name servers)",
                    tool.name,
                )
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
