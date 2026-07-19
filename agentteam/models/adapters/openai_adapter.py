"""OpenAI 适配器。"""
from __future__ import annotations

from agentteam.models.adapters.base import BaseAdapter


class OpenAIAdapter(BaseAdapter):
    provider_name = "openai"
    env_var = "OPENAI_API_KEY"
    chat_class_path = "langchain_openai.ChatOpenAI"
