import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def tmp_db(tmp_path: Path) -> sqlite3.Connection:
    """提供一个临时 SQLite 连接，测试结束自动关闭。"""
    from agentteam.storage.db import init_db

    conn = init_db(tmp_path / "test.db")
    yield conn
    conn.close()


class FakeLLM:
    """测试用假 LLM。

    invoke() 按顺序返回 invoke_responses 中的元素；
    with_structured_output().invoke() 按顺序返回 structured_responses 中的元素。
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
    """测试用模型提供者，按 model name 映射到不同 FakeLLM。"""

    def __init__(self, llm_by_model_name: dict[str, FakeLLM]) -> None:
        self._map = llm_by_model_name

    def get_llm(self, ref):
        return self._map[ref.name]


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def fake_trace_writer():
    from agentteam.runtime.trace import FakeTraceWriter
    return FakeTraceWriter()
