import pytest

from agentteam.models.provider import ModelRef


def test_anthropic_adapter_builds(monkeypatch):
    import agentteam.models.adapters.anthropic as mod

    captured = {}

    class FakeChatAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(mod, "_load_chat_class", lambda: FakeChatAnthropic)

    adapter = mod.AnthropicAdapter({"anthropic": "sk-ant-fake"})
    ref = ModelRef(provider="anthropic", name="claude-3-5-sonnet-20240620", temperature=0.3, streaming=False)
    llm = adapter.build(ref)

    assert isinstance(llm, FakeChatAnthropic)
    assert captured["model"] == "claude-3-5-sonnet-20240620"
    assert captured["temperature"] == 0.3
    assert captured["api_key"] == "sk-ant-fake"


def test_anthropic_adapter_missing_key(monkeypatch):
    import agentteam.models.adapters.anthropic as mod

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(mod, "_load_chat_class", lambda: type("F", (), {"__init__": lambda self, **k: None}))

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        mod.AnthropicAdapter({}).build(ModelRef(provider="anthropic", name="claude-3-5-sonnet-20240620"))
