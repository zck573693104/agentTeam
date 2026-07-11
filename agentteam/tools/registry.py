from __future__ import annotations

from langchain_core.tools import BaseTool


class ToolRegistry:
    """工具统一注册表。Worker 配置里按名字引用工具，运行时取出绑定到 LLM。"""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get_tools(self, names: list[str]) -> list[BaseTool]:
        missing = [n for n in names if n not in self._tools]
        if missing:
            raise KeyError(f"Tools not found: {missing}")
        return [self._tools[n] for n in names]

    def list_names(self) -> list[str]:
        return list(self._tools.keys())
