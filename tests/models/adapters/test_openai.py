import pytest

from agentteam.models.provider import ModelRef


def test_openai_adapter_builds(monkeypatch):
    import agentteam.models.adapters.openai_adapter as mod

    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(mod, "_load_chat_class", lambda: FakeChatOpenAI)

    adapter = mod.OpenAIAdapter({"openai": "sk-fake"})
    ref = ModelRef(provider="openai", name="gpt-4o", temperature=0.2, streaming=True)
    llm = adapter.build(ref)

    assert isinstance(llm, FakeChatOpenAI)
    assert captured["model"] == "gpt-4o"
    assert captured["temperature"] == 0.2
    assert captured["api_key"] == "sk-fake"
    assert captured["streaming"] is True


def test_openai_adapter_env_key(monkeypatch):
    import agentteam.models.adapters.openai_adapter as mod

    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.setattr(mod, "_load_chat_class", lambda: type("F", (), {"__init__": lambda self, **k: None}))

    mod.OpenAIAdapter({}).build(ModelRef(provider="openai", name="gpt-4o"))


def test_openai_adapter_missing_key(monkeypatch):
    import agentteam.models.adapters.openai_adapter as mod

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(mod, "_load_chat_class", lambda: type("F", (), {"__init__": lambda self, **k: None}))

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        mod.OpenAIAdapter({}).build(ModelRef(provider="openai", name="gpt-4o"))
