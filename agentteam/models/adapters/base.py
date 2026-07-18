"""Model provider adapter 抽象基类。

所有具体 adapter（QwenAdapter/OpenAIAdapter/AnthropicAdapter/OllamaAdapter）应继承
BaseAdapter 并实现 build。第三方 provider 可继承此类并通过 ModelProvider.register 注册。

ABC 基于 abc.ABCMeta（class BaseAdapter(ABC) 等价于 metaclass=ABCMeta）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from langchain_core.language_models import BaseChatModel

from ..provider import ModelRef


class BaseAdapter(ABC):
    """Model provider adapter 协议。

    子类必须实现 build(ref) -> BaseChatModel。
    __init__ 接收 api_keys dict，子类可覆盖以自定义配置存储（如 OllamaAdapter 用 _config）。
    """

    def __init__(self, api_keys: dict[str, str]) -> None:
        self._api_keys = api_keys

    @abstractmethod
    def build(self, ref: ModelRef) -> BaseChatModel:
        """根据 ModelRef 构造 LangChain BaseChatModel 实例。"""
