import pytest
from langchain_core.tools import StructuredTool


def _make_tool(name: str) -> StructuredTool:
    return StructuredTool.from_function(name=name, description=f"tool {name}", func=lambda: name)


def test_register_and_get():
    from agentteam.tools.registry import ToolRegistry

    reg = ToolRegistry()
    t = _make_tool("foo")
    reg.register(t)
    assert reg.get_tools(["foo"]) == [t]


def test_register_duplicate_raises():
    from agentteam.tools.registry import ToolRegistry

    reg = ToolRegistry()
    reg.register(_make_tool("foo"))
    with pytest.raises(ValueError, match="already registered"):
        reg.register(_make_tool("foo"))


def test_get_missing_raises():
    from agentteam.tools.registry import ToolRegistry

    reg = ToolRegistry()
    with pytest.raises(KeyError, match="not found"):
        reg.get_tools(["nope"])


def test_list_names():
    from agentteam.tools.registry import ToolRegistry

    reg = ToolRegistry()
    reg.register(_make_tool("a"))
    reg.register(_make_tool("b"))
    assert set(reg.list_names()) == {"a", "b"}


def test_register_mcp_tools_with_fake_loader():
    """register_mcp_tools 用注入的 loader 加载工具，加 mcp: 前缀。"""
    from agentteam.domain.mcp_server import MCPServer
    from agentteam.tools.registry import ToolRegistry

    fake_tools = [_make_tool("fetch"), _make_tool("search")]
    fake_loader = lambda server: fake_tools  # noqa: E731

    reg = ToolRegistry(mcp_loader=fake_loader)
    server = MCPServer(name="remote", command="python")
    registered = reg.register_mcp_tools(server)

    assert set(registered) == {"mcp:remote:fetch", "mcp:remote:search"}
    assert set(reg.list_names()) == {"mcp:remote:fetch", "mcp:remote:search"}


def test_register_mcp_tools_get_by_prefixed_name():
    """注册后可用 mcp: 前缀名获取工具。"""
    from agentteam.domain.mcp_server import MCPServer
    from agentteam.tools.registry import ToolRegistry

    fake_tools = [_make_tool("fetch")]
    reg = ToolRegistry(mcp_loader=lambda s: fake_tools)
    server = MCPServer(name="srv", command="python")
    reg.register_mcp_tools(server)

    tools = reg.get_tools(["mcp:srv:fetch"])
    assert len(tools) == 1
    assert tools[0].name == "mcp:srv:fetch"


def test_register_mcp_tools_empty_returns_empty_list():
    """MCP server 无工具时返回空列表，不报错。"""
    from agentteam.domain.mcp_server import MCPServer
    from agentteam.tools.registry import ToolRegistry

    reg = ToolRegistry(mcp_loader=lambda s: [])
    server = MCPServer(name="empty", command="python")
    registered = reg.register_mcp_tools(server)
    assert registered == []
