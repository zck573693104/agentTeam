"""阿里通义千问 Qwen 适配器(走 langchain_community.chat_models.ChatTongyi)。"""
from __future__ import annotations

import os

from agentteam.models.adapters.base import BaseAdapter


class QwenAdapter(BaseAdapter):
    provider_name = "qwen"
    env_var = "DASHSCOPE_API_KEY"
    chat_class_path = "langchain_community.chat_models.ChatTongyi"

    def build(self, ref):
        """Qwen 用 dashscope_api_key 而非 api_key,api_keys dict 用 'dashscope' 键。"""
        ChatClass = self._load_chat_class()
        api_key = self._api_keys.get("dashscope") or os.environ.get(self.env_var)
        if not api_key:
            raise ValueError(
                f"未提供 {self.env_var},请在 api_keys 或环境变量中设置"
            )
        kwargs = {
            "model": ref.name,
            "temperature": ref.temperature,
            "dashscope_api_key": api_key,
            "streaming": ref.streaming,
        }
        return ChatClass(**kwargs)
