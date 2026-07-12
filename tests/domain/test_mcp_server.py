from agentteam.domain.mcp_server import MCPServer


def test_mcp_server_stdio_defaults():
    """stdio 模式的 MCPServer 有合理默认值。"""
    server = MCPServer(name="fetch", command="python")
    assert server.name == "fetch"
    assert server.command == "python"
    assert server.args == []
    assert server.env == {}
    assert server.transport == "stdio"
    assert server.url is None


def test_mcp_server_with_args_and_env():
    """MCPServer 支持 args 和 env。"""
    server = MCPServer(
        name="git",
        command="uvx",
        args=["mcp-server-git"],
        env={"GIT_REPO": "/tmp/repo"},
    )
    assert server.args == ["mcp-server-git"]
    assert server.env["GIT_REPO"] == "/tmp/repo"


def test_mcp_server_http_transport():
    """HTTP 模式的 MCPServer 使用 url 而非 command。"""
    server = MCPServer(
        name="remote",
        command="",
        transport="http",
        url="http://localhost:8080/mcp",
    )
    assert server.transport == "http"
    assert server.url == "http://localhost:8080/mcp"
