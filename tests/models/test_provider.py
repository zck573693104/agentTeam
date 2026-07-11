import pytest

from agentteam.models.provider import ModelRef, ModelProvider


def test_model_ref_defaults():
    ref = ModelRef(provider="qwen", name="qwen-max")
    assert ref.provider == "qwen"
    assert ref.name == "qwen-max"
    assert ref.temperature == 0.7
    assert ref.streaming is True


def test_model_ref_is_frozen():
    ref = ModelRef(provider="openai", name="gpt-4o")
    with pytest.raises(Exception):
        ref.provider = "anthropic"  # type: ignore[misc]


def test_provider_unknown_raises():
    provider = ModelProvider()
    ref = ModelRef(provider="qwen", name="qwen-max")  # provider 合法但未注入适配器时由分发处理
    # 用一个非法 provider 值绕过 Literal 检查
    bad = ModelRef(provider="qwen", name="x")
    object.__setattr__(bad, "provider", "unknown")
    with pytest.raises(ValueError, match="Unknown provider"):
        provider.get_llm(bad)
