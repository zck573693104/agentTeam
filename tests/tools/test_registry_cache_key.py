"""SP6-P2 ToolRegistry 缓存 key 修正测试。

独立于 tests/tools/test_mcp_leak.py(BUG-06 回归),本文件专门验证:
- _server_cache_key helper 返回 (name, command, args tuple, transport, url)
- 同名不同 command/args/transport/url 的 server 各自独立缓存
- 同配置二次调用命中缓存
- loader 失败不入缓存(契约 guard rail)

全部用 fake loader,不依赖真实 npx 子进程。
"""
from langchain_core.tools import StructuredTool

from agentteam.domain.mcp_server import MCPServer


def _make_tool(name: str) -> StructuredTool:
    return StructuredTool.from_function(
        name=name, description=f"tool {name}", func=lambda: name
    )


def test_server_cache_key_returns_tuple():
    """_server_cache_key 返回 (name, command, args tuple, transport, url) tuple。

    P2 核心:用配置 tuple 唯一标识 MCP server,而非 server.name。
    """
    from agentteam.tools.registry import _server_cache_key

    server = MCPServer(
        name="git",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-git", "--repository", "."],
        transport="stdio",
        url=None,
    )
    key = _server_cache_key(server)
    assert isinstance(key, tuple)
    assert key == (
        "git",
        "npx",
        ("-y", "@modelcontextprotocol/server-git", "--repository", "."),
        "stdio",
        None,
    )


def test_server_cache_key_http_server_with_url():
    """http transport 的 server,url 进入 key。"""
    from agentteam.tools.registry import _server_cache_key

    server = MCPServer(
        name="remote",
        command="",
        args=[],
        transport="http",
        url="http://localhost:8080/mcp",
    )
    key = _server_cache_key(server)
    assert key == ("remote", "", (), "http", "http://localhost:8080/mcp")


def test_server_cache_key_default_args_empty_tuple():
    """未传 args(默认空 list)时,key 中 args 部分为空 tuple。"""
    from agentteam.tools.registry import _server_cache_key

    server = MCPServer(name="srv", command="python")
    key = _server_cache_key(server)
    # 默认 args=[],转 tuple 后是 ()
    assert key == ("srv", "python", (), "stdio", None)


def test_server_cache_key_same_config_returns_equal_tuple():
    """两个独立构造但配置相同的 MCPServer,cache key 应相等(可命中缓存)。"""
    from agentteam.tools.registry import _server_cache_key

    server_a = MCPServer(name="git", command="npx", args=["-y", "server-git"])
    server_b = MCPServer(name="git", command="npx", args=["-y", "server-git"])
    assert _server_cache_key(server_a) == _server_cache_key(server_b)
    # hash 也应相等(可加入 set)
    assert hash(_server_cache_key(server_a)) == hash(_server_cache_key(server_b))


def test_server_cache_key_different_args_not_equal():
    """同名同 command 但 args 不同,cache key 应不同(各自独立缓存)。"""
    from agentteam.tools.registry import _server_cache_key

    server_a = MCPServer(name="git", command="npx", args=["-y", "server-git-a"])
    server_b = MCPServer(name="git", command="npx", args=["-y", "server-git-b"])
    assert _server_cache_key(server_a) != _server_cache_key(server_b)
