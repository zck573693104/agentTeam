from __future__ import annotations

import os
from typing import Callable

from langchain_core.language_models import BaseChatModel

from ..provider import ModelRef


def _load_chat_class() -> Callable:
    """懒加载 ChatOllama，缺失依赖时给清晰错误。"""
    try:
        from langchain_ollama import ChatOllama
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "Ollama 适配器需要安装 langchain-ollama：pip install 'agentteam[ollama]'"
        ) from e
    return ChatOllama


class OllamaAdapter:
    def __init__(self, api_keys: dict[str, str]) -> None:
        # 复用 api_keys dict 承载 ollama_base_url 等本地配置
        self._config = api_keys

    def build(self, ref: ModelRef) -> BaseChatModel:
        ChatOllama = _load_chat_class()
        base_url = self._config.get("ollama_base_url") or os.environ.get("OLLAMA_BASE_URL")
        kwargs: dict[str, object] = {
            "model": ref.name,
            "temperature": ref.temperature,
            "streaming": ref.streaming,
        }
        if base_url:
            kwargs["base_url"] = base_url
        return ChatOllama(**kwargs)
