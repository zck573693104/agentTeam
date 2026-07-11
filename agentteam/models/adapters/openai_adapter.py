from __future__ import annotations

import os
from typing import Callable

from langchain_core.language_models import BaseChatModel

from ..provider import ModelRef


def _load_chat_class() -> Callable:
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "OpenAI 适配器需要安装 langchain-openai：pip install 'agentteam[openai]'"
        ) from e
    return ChatOpenAI


class OpenAIAdapter:
    def __init__(self, api_keys: dict[str, str]) -> None:
        self._api_keys = api_keys

    def build(self, ref: ModelRef) -> BaseChatModel:
        ChatOpenAI = _load_chat_class()
        api_key = self._api_keys.get("openai") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("未提供 OPENAI_API_KEY")
        return ChatOpenAI(
            model=ref.name,
            temperature=ref.temperature,
            api_key=api_key,
            streaming=ref.streaming,
        )
