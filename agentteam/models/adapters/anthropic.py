"""Anthropic Claude 适配器。"""
from __future__ import annotations

from agentteam.models.adapters.base import BaseAdapter


class AnthropicAdapter(BaseAdapter):
    provider_name = "anthropic"
    env_var = "ANTHROPIC_API_KEY"
    chat_class_path = "langchain_anthropic.ChatAnthropic"
