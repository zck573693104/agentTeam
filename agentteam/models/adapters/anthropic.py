from __future__ import annotations

import os
from typing import Callable

from langchain_core.language_models import BaseChatModel

from ..provider import ModelRef


def _load_chat_class() -> Callable:
    """懒加载 ChatAnthropic，缺失依赖时给清晰错误。"""
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "Anthropic 适配器需要安装 langchain-anthropic：pip install 'agentteam[anthropic]'"
        ) from e
    return ChatAnthropic


class AnthropicAdapter:
    def __init__(self, api_keys: dict[str, str]) -> None:
        self._api_keys = api_keys

    def build(self, ref: ModelRef) -> BaseChatModel:
        ChatAnthropic = _load_chat_class()
        api_key = self._api_keys.get("anthropic") or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("未提供 ANTHROPIC_API_KEY")
        return ChatAnthropic(
            model=ref.name,
            temperature=ref.temperature,
            api_key=api_key,
            streaming=ref.streaming,
        )
