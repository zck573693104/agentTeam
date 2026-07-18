from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from langchain_core.language_models import BaseChatModel

ProviderName = Literal["qwen", "openai", "anthropic", "ollama"]


@dataclass(frozen=True)
class ModelRef:
    """对某个模型的引用，Worker 配置里用它指定模型。"""

    provider: ProviderName
    name: str
    temperature: float = 0.7
    streaming: bool = True


class ModelProvider:
    """把 ModelRef 解析成具体的 LangChain BaseChatModel。

    适配器按需懒加载，缺失依赖时由适配器抛出清晰错误。
    Provider 通过 class-level _registry 注册，新增 provider 无需修改本类源码（开闭原则）。
    """

    _registry: dict[str, type] = {}

    @classmethod
    def register(cls, name: str, adapter_cls: type) -> None:
        """注册 adapter 类。第三方 provider 在 import 时调用此方法。

        重名注册抛 ValueError，防止意外覆盖。
        """
        if name in cls._registry:
            raise ValueError(f"Provider already registered: {name}")
        cls._registry[name] = adapter_cls

    @classmethod
    def list_providers(cls) -> list[str]:
        """返回所有已注册 provider name。"""
        return list(cls._registry.keys())

    def __init__(self, api_keys: dict[str, str] | None = None) -> None:
        self._api_keys = api_keys or {}
        # 延迟 import adapters 包并显式注册内置 adapter。
        # 放在此处而非模块顶层，避免循环依赖（adapters/__init__.py 反向 import ModelProvider）。
        # register_builtins() 是幂等的：clean_registry fixture 清空 _registry 后，
        # 下次 ModelProvider() 调用会重新注册，保证测试隔离。
        from agentteam.models import adapters
        adapters.register_builtins()

    def get_llm(self, ref: ModelRef) -> BaseChatModel:
        adapter_cls = self._registry.get(ref.provider)
        if adapter_cls is None:
            raise ValueError(
                f"Unknown provider: {ref.provider}. "
                f"Registered: {self.list_providers()}"
            )
        return adapter_cls(self._api_keys).build(ref)
