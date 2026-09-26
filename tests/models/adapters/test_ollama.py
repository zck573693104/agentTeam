from agentteam.models.provider import ModelRef


def test_ollama_adapter_builds(monkeypatch):
    import agentteam.models.adapters.ollama as mod

    captured = {}

    class FakeChatOllama:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(mod.OllamaAdapter, "_load_chat_class", lambda self: FakeChatOllama)

    adapter = mod.OllamaAdapter({})
    ref = ModelRef(provider="ollama", name="llama3", temperature=0.8, streaming=False)
    llm = adapter.build(ref)

    assert isinstance(llm, FakeChatOllama)
    assert captured["model"] == "llama3"
    assert captured["temperature"] == 0.8
    assert captured["streaming"] is False
    # Ollama 本地运行，无需 api_key
    assert "api_key" not in captured


def test_ollama_adapter_custom_base_url(monkeypatch):
    import agentteam.models.adapters.ollama as mod

    captured = {}

    class FakeChatOllama:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(mod.OllamaAdapter, "_load_chat_class", lambda self: FakeChatOllama)

    adapter = mod.OllamaAdapter({"ollama_base_url": "http://host:11434"})
    adapter.build(ModelRef(provider="ollama", name="llama3"))

    assert captured["base_url"] == "http://host:11434"
