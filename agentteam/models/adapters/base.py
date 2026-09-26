"""BaseAdapter: 模型适配器抽象基类。"""
from __future__ import annotations

import importlib
import os
from abc import ABC, abstractmethod

from langchain_core.language_models import BaseChatModel

from agentteam.models.provider import ModelRef


class BaseAdapter(ABC):
    """模型适配器基类。

    子类通过类属性声明 provider 元数据,默认 build() 实现走 api_key 流程:
      - provider_name: 用于读取 self._api_keys[provider_name] 或 env_var
      - env_var: API key 环境变量名
      - chat_class_path: "module.path.ClassName" 形式,懒加载
      - extra_build_kwargs: 额外传给 chat class 的固定 kwargs (e.g. {"streaming": True})

    子类若需要不同行为(如 Ollama 用 base_url 而非 api_key),重写 build() 即可。
    """

    # 子类必须覆盖以下类属性
    provider_name: str = ""
    env_var: str = ""
    chat_class_path: str = ""
    extra_build_kwargs: dict = {}

    def __init__(self, api_keys: dict[str, str] | None = None) -> None:
        self._api_keys = api_keys or {}

    def _load_chat_class(self):
        """懒加载 chat class,缺失依赖时抛 ImportError 带清晰错误。"""
        module_path, _, class_name = self.chat_class_path.rpartition(".")
        try:
            module = importlib.import_module(module_path)
            return getattr(module, class_name)
        except (ImportError, AttributeError) as e:
            raise ImportError(
                f"{self.provider_name} adapter 需要安装依赖: {e}. "
                f"请 pip install -e \".[{self.provider_name}]\""
            )

    def build(self, ref: ModelRef) -> BaseChatModel:
        """默认实现:从 api_keys/env_var 取 key,构造 chat class。"""
        ChatClass = self._load_chat_class()
        api_key = self._api_keys.get(self.provider_name) or os.environ.get(self.env_var)
        if not api_key:
            raise ValueError(
                f"未提供 {self.env_var},请在 api_keys 或环境变量中设置"
            )
        kwargs = {
            "model": ref.name,
            "temperature": ref.temperature,
            "api_key": api_key,
            "streaming": ref.streaming,
            **self.extra_build_kwargs,
        }
        return ChatClass(**kwargs)
