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


def test_unregister_existing_tool_returns_true():
    """unregister 已注册工具返回 True,且工具不再可用。"""
    from agentteam.tools.registry import ToolRegistry

    reg = ToolRegistry()
    reg.register(_make_tool("foo"))
    assert reg.unregister("foo") is True
    assert "foo" not in reg.list_names()
    with pytest.raises(KeyError):
        reg.get_tools(["foo"])


def test_unregister_missing_tool_returns_false():
    """unregister 不存在的工具返回 False,不报错。"""
    from agentteam.tools.registry import ToolRegistry

    reg = ToolRegistry()
    assert reg.unregister("nope") is False


def test_register_mcp_tools_idempotent():
    """重复调用 register_mcp_tools 同一 server 不报错，幂等跳过。"""
    from agentteam.domain.mcp_server import MCPServer
    from agentteam.tools.registry import ToolRegistry

    # 每次调用返回新工具对象（模拟真实 MCP loader 行为）
    def fake_loader(server):
        return [_make_tool("fetch"), _make_tool("search")]

    reg = ToolRegistry(mcp_loader=fake_loader)
    server = MCPServer(name="remote", command="python")

    # 第一次注册
    registered1 = reg.register_mcp_tools(server)
    assert set(registered1) == {"mcp:remote:fetch", "mcp:remote:search"}

    # 第二次注册——不报错，幂等跳过
    registered2 = reg.register_mcp_tools(server)
    assert set(registered2) == {"mcp:remote:fetch", "mcp:remote:search"}
    assert set(reg.list_names()) == {"mcp:remote:fetch", "mcp:remote:search"}
