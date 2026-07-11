from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from langchain_core.language_models import BaseChatModel

ProviderName = Literal["qwen", "openai", "anthropic", "ollama"]


@dataclass(frozen=True)
class ModelRef:
    """对某个模型的引用，Worker 配置里用它指定模型。"""

    provider: ProviderName
    name: str
    temperature: float = 0.7
    streaming: bool = True


class ModelProvider:
    """把 ModelRef 解析成具体的 LangChain BaseChatModel。

    适配器按需懒加载，缺失依赖时由适配器抛出清晰错误。
    """

    def __init__(self, api_keys: dict[str, str] | None = None) -> None:
        self._api_keys = api_keys or {}

    def get_llm(self, ref: ModelRef) -> BaseChatModel:
        if ref.provider == "qwen":
            from .adapters.qwen import QwenAdapter

            return QwenAdapter(self._api_keys).build(ref)
        if ref.provider == "openai":
            from .adapters.openai_adapter import OpenAIAdapter

            return OpenAIAdapter(self._api_keys).build(ref)
        if ref.provider == "anthropic":
            from .adapters.anthropic import AnthropicAdapter

            return AnthropicAdapter(self._api_keys).build(ref)
        if ref.provider == "ollama":
            from .adapters.ollama import OllamaAdapter

            return OllamaAdapter(self._api_keys).build(ref)
        raise ValueError(f"Unknown provider: {ref.provider}")
