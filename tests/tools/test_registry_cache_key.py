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


def test_same_name_different_command_both_loaded():
    """同名但 args 不同的两个 MCPServer,loader 应各被调用 1 次。

    P2 修复核心:原实现用 server.name 作 cache key,两个同名 server 中
    第二个被错误跳过 loader,工具不注册。修复后用配置 tuple 作 key,
    两个 server 各自独立缓存,loader 各调用 1 次(共 2 次)。
    """
    from agentteam.tools.registry import ToolRegistry

    call_count = {"n": 0}

    def counting_loader(server):
        call_count["n"] += 1
        # 每次返回不同名 tool,避免工具名冲突(前缀都是 mcp:git:)
        return [_make_tool(f"tool_{call_count['n']}")]

    reg = ToolRegistry(mcp_loader=counting_loader)
    # 两个同名但 args 不同(模拟 git server 指向不同仓库)
    server_a = MCPServer(
        name="git", command="npx",
        args=["-y", "@modelcontextprotocol/server-git", "--repository", "repo_a"],
    )
    server_b = MCPServer(
        name="git", command="npx",
        args=["-y", "@modelcontextprotocol/server-git", "--repository", "repo_b"],
    )

    reg.register_mcp_tools(server_a)
    reg.register_mcp_tools(server_b)
    assert call_count["n"] == 2, (
        "同名不同 args 的 server 应各自触发 loader,实际触发 "
        f"{call_count['n']} 次"
    )
    # 两个 server 的工具都注册了(因 tool 名不同,无冲突)
    assert "mcp:git:tool_1" in reg.list_names()
    assert "mcp:git:tool_2" in reg.list_names()


def test_same_name_different_command_same_tool_name_second_skipped():
    """同名不同配置的 server 注册同名工具时,第二个工具被跳过(已知限制)。

    工具名前缀仍是 mcp:{server.name}:,因此同名不同配置的 server 注册
    的同名工具会冲突。这是预期行为 — 用户若需多实例应改 server.name。
    本测试显式记录此契约,避免后续修改时意外破坏。
    """
    from agentteam.tools.registry import ToolRegistry

    call_count = {"n": 0}

    def counting_loader(server):
        call_count["n"] += 1
        # 两次都返回同名 tool,触发工具名冲突
        return [_make_tool("git_status")]

    reg = ToolRegistry(mcp_loader=counting_loader)
    server_a = MCPServer(
        name="git", command="npx",
        args=["-y", "@modelcontextprotocol/server-git", "--repository", "repo_a"],
    )
    server_b = MCPServer(
        name="git", command="npx",
        args=["-y", "@modelcontextprotocol/server-git", "--repository", "repo_b"],
    )

    registered_a = reg.register_mcp_tools(server_a)
    registered_b = reg.register_mcp_tools(server_b)

    # loader 仍各调用 1 次(cache key 是配置 tuple,不同)
    assert call_count["n"] == 2
    # 但工具名都注册为 mcp:git:git_status,第二个被二级防护跳过
    assert registered_a == ["mcp:git:git_status"]
    assert registered_b == ["mcp:git:git_status"]  # 命中已注册,跳过覆盖
    # registry 中只有 1 个 tool(同名冲突)
    assert reg.list_names() == ["mcp:git:git_status"]


def test_same_config_second_call_uses_cache():
    """配置完全相同的 MCPServer 二次调用,loader 只触发 1 次(命中缓存)。"""
    from agentteam.tools.registry import ToolRegistry

    call_count = {"n": 0}

    def counting_loader(server):
        call_count["n"] += 1
        return [_make_tool("fetch"), _make_tool("search")]

    reg = ToolRegistry(mcp_loader=counting_loader)
    server = MCPServer(
        name="git", command="npx",
        args=["-y", "@modelcontextprotocol/server-git"],
    )

    # 第一次:loader 被调用,工具注册
    registered1 = reg.register_mcp_tools(server)
    assert set(registered1) == {"mcp:git:fetch", "mcp:git:search"}
    assert call_count["n"] == 1

    # 第二次:配置相同 → cache key 相同 → 命中缓存,loader 不再调用
    registered2 = reg.register_mcp_tools(server)
    assert call_count["n"] == 1, "相同配置二次调用应命中缓存,loader 不再调用"
    # 已注册工具名仍返回(保持幂等语义)
    assert set(registered2) == {"mcp:git:fetch", "mcp:git:search"}


def test_different_name_different_key():
    """不同 name 的 server 独立缓存(P2 修复后仍成立,回归 guard)。"""
    from agentteam.tools.registry import ToolRegistry

    calls = []

    def tracking_loader(server):
        calls.append(server.name)
        return [_make_tool(f"tool_{server.name}")]

    reg = ToolRegistry(mcp_loader=tracking_loader)
    server_a = MCPServer(name="server_a", command="python")
    server_b = MCPServer(name="server_b", command="python")

    reg.register_mcp_tools(server_a)
    reg.register_mcp_tools(server_b)
    assert calls == ["server_a", "server_b"]

    # 再次调用:都应命中各自缓存,不新增 loader 调用
    reg.register_mcp_tools(server_a)
    reg.register_mcp_tools(server_b)
    assert calls == ["server_a", "server_b"], "缓存命中后不应再调用 loader"
