import pytest

from agentteam.models.provider import ModelRef


def test_qwen_adapter_builds_with_explicit_key(monkeypatch):
    import agentteam.models.adapters.qwen as qwen

    captured = {}

    class FakeChatTongyi:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(qwen, "_load_chat_class", lambda: FakeChatTongyi)

    adapter = qwen.QwenAdapter({"dashscope": "fake-key"})
    ref = ModelRef(provider="qwen", name="qwen-max", temperature=0.5, streaming=False)
    llm = adapter.build(ref)

    assert isinstance(llm, FakeChatTongyi)
    assert captured["model"] == "qwen-max"
    assert captured["temperature"] == 0.5
    assert captured["dashscope_api_key"] == "fake-key"
    assert captured["streaming"] is False


def test_qwen_adapter_uses_env_key(monkeypatch):
    import agentteam.models.adapters.qwen as qwen

    monkeypatch.setenv("DASHSCOPE_API_KEY", "env-key")
    monkeypatch.setattr(qwen, "_load_chat_class", lambda: type("Fake", (), {"__init__": lambda self, **k: None}))

    adapter = qwen.QwenAdapter({})
    ref = ModelRef(provider="qwen", name="qwen-max")
    adapter.build(ref)  # 不应抛错


def test_qwen_adapter_missing_key_raises(monkeypatch):
    import agentteam.models.adapters.qwen as qwen

    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setattr(qwen, "_load_chat_class", lambda: type("Fake", (), {"__init__": lambda self, **k: None}))

    adapter = qwen.QwenAdapter({})
    with pytest.raises(ValueError, match="DASHSCOPE_API_KEY"):
        adapter.build(ModelRef(provider="qwen", name="qwen-max"))
