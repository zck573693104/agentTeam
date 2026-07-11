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
