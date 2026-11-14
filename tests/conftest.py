"""共享测试夹具:FakeLLM/FakeModelProvider(v2 内核的窄模型接口)。"""
from pathlib import Path

import pytest


class FakeLLM:
    """测试用假 LLM。

    invoke() 按顺序返回 invoke_responses;with_structured_output().invoke()
    按顺序返回 structured_responses。窄接口(invoke/bind_tools/
    with_structured_output)与生产 ModelProvider 契约对齐。
    """

    def __init__(self) -> None:
        self.invoke_responses: list = []
        self.structured_responses: list = []
        self._inv_idx = 0
        self._struct_idx = 0

    def set_invoke_responses(self, responses: list) -> None:
        self.invoke_responses = list(responses)
        self._inv_idx = 0

    def set_structured_responses(self, responses: list) -> None:
        self.structured_responses = list(responses)
        self._struct_idx = 0

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, schema, **kwargs):
        parent = self

        class _Structured:
            def invoke(self, messages, **kw):
                r = parent.structured_responses[parent._struct_idx]
                parent._struct_idx += 1
                return r

        return _Structured()

    def invoke(self, messages, **kwargs):
        r = self.invoke_responses[self._inv_idx]
        self._inv_idx += 1
        return r


class FakeModelProvider:
    """按 model name 映射 FakeLLM 的测试提供者。"""

    def __init__(self, llm_by_model_name: dict[str, FakeLLM]) -> None:
        self._map = llm_by_model_name

    def get_llm(self, ref):
        return self._map[ref.name]


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def fake_mcp_loader():
    """fake MCP loader:产出指定工具列表。"""
    from langchain_core.tools import StructuredTool

    def _loader(tools=None):
        tools = tools or []

        def loader(server):
            return list(tools)

        return loader

    return _loader
