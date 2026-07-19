"""Ollama 本地模型适配器(用 base_url,不需 api_key)。"""
from __future__ import annotations

import os

from agentteam.models.adapters.base import BaseAdapter


class OllamaAdapter(BaseAdapter):
    provider_name = "ollama"
    env_var = "OLLAMA_BASE_URL"  # 借用 env_var 字段表示配置环境变量
    chat_class_path = "langchain_ollama.ChatOllama"

    def build(self, ref):
        """Ollama 用 base_url,无 api_key 校验。"""
        ChatClass = self._load_chat_class()
        base_url = self._api_keys.get("ollama_base_url") or os.environ.get("OLLAMA_BASE_URL")
        kwargs = {
            "model": ref.name,
            "temperature": ref.temperature,
            "streaming": ref.streaming,
            "base_url": base_url,
        }
        return ChatClass(**kwargs)
