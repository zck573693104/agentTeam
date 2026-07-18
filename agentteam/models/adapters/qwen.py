from __future__ import annotations

import os
from typing import Callable

from langchain_core.language_models import BaseChatModel

from .base import BaseAdapter
from ..provider import ModelRef


def _load_chat_class() -> Callable:
    """懒加载 ChatTongyi，缺失依赖时给清晰错误。"""
    try:
        from langchain_community.chat_models import ChatTongyi
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "Qwen 适配器需要安装 dashscope 和 langchain-community："
            "pip install 'agentteam[qwen]'"
        ) from e
    return ChatTongyi


class QwenAdapter(BaseAdapter):
    def build(self, ref: ModelRef) -> BaseChatModel:
        ChatTongyi = _load_chat_class()
        api_key = self._api_keys.get("dashscope") or os.environ.get("DASHSCOPE_API_KEY")
        if not api_key:
            raise ValueError("未提供 DASHSCOPE_API_KEY（通过 api_keys 或环境变量）")
        return ChatTongyi(
            model=ref.name,
            temperature=ref.temperature,
            dashscope_api_key=api_key,
            streaming=ref.streaming,
        )
