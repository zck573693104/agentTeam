"""BUG-06 回归测试:MCP 子进程泄漏缓存。

每次 create_run → compile → register_mcp_tools 都不应重复 spawn MCP 子进程。
ToolRegistry 缓存已加载的 server.name,loader 只调用一次。
"""
import pytest
from langchain_core.tools import StructuredTool

from agentteam.domain.mcp_server import MCPServer
from agentteam.tools.registry import ToolRegistry


def _make_tool(name: str) -> StructuredTool:
    return StructuredTool.from_function(
        name=name, description=f"tool {name}", func=lambda: name
    )


def test_register_mcp_tools_caches_server():
    """同一 server 调用两次 register_mcp_tools,loader 只调用 1 次。

    BUG-06:原实现每次都调用 loader(会 spawn npx 子进程),
    即使工具名幂等跳过,loader 调用本身不幂等,导致子进程泄漏。
    修复后:_loaded_servers 缓存 server.name,二次调用直接跳过 loader。
    """
    call_count = {"n": 0}

    def counting_loader(server):
        call_count["n"] += 1
        return [_make_tool("fetch"), _make_tool("search")]

    reg = ToolRegistry(mcp_loader=counting_loader)
    server = MCPServer(name="remote", command="python")

    # 第一次:loader 被调用,工具注册
    registered1 = reg.register_mcp_tools(server)
    assert set(registered1) == {"mcp:remote:fetch", "mcp:remote:search"}
    assert call_count["n"] == 1

    # 第二次:loader 不应再被调用(命中缓存)
    registered2 = reg.register_mcp_tools(server)
    assert call_count["n"] == 1, "loader 不应被重复调用"
    # 已注册工具名仍返回(保持幂等语义,不破坏现有调用方)
    assert set(registered2) == {"mcp:remote:fetch", "mcp:remote:search"}


def test_register_mcp_tools_different_servers():
    """不同 server 的 loader 调用互不干扰。"""
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


def test_register_mcp_tools_loader_failure_not_cached():
    """loader 抛异常时不缓存,允许重试。

    修复方案要求:loader 调用失败时不加入缓存(允许重试)。
    此测试为 guard rail:确保实现不会在 loader 调用前就缓存,
    或在异常路径也加入缓存。
    """
    call_count = {"n": 0}

    def failing_then_success_loader(server):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("load failed")
        return [_make_tool("fetch")]

    reg = ToolRegistry(mcp_loader=failing_then_success_loader)
    server = MCPServer(name="srv", command="python")

    # 第一次:loader 抛异常
    with pytest.raises(RuntimeError, match="load failed"):
        reg.register_mcp_tools(server)

    # 第二次:loader 应再次被调用(失败未缓存)
    registered = reg.register_mcp_tools(server)
    assert call_count["n"] == 2, "失败后应允许重试,loader 应被再次调用"
    assert "mcp:srv:fetch" in registered
