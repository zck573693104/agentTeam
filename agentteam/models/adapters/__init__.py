"""Model adapter 包 — 提供 register_builtins() 显式注册内置 adapter。

循环依赖说明：
- 本模块 import ModelProvider（来自 ..provider）
- provider.py 不在顶层 import 本包，只在 ModelProvider.__init__ 内延迟 import
- 因此无循环：provider.py 模块加载完成 → 后续 ModelProvider() 实例化触发本包 import → 调 register_builtins()

第三方 provider 可在自身模块 import 时调用 ModelProvider.register 接入。
"""
from agentteam.models.provider import ModelProvider

from .qwen import QwenAdapter
from .openai_adapter import OpenAIAdapter
from .anthropic import AnthropicAdapter
from .ollama import OllamaAdapter

# 内置 adapter 注册表（name → adapter class）
_BUILTIN_ADAPTERS: dict[str, type] = {
    "qwen": QwenAdapter,
    "openai": OpenAIAdapter,
    "anthropic": AnthropicAdapter,
    "ollama": OllamaAdapter,
}


def register_builtins() -> None:
    """注册内置 adapter（幂等）。

    幂等设计：已注册的 name 跳过（不抛 ValueError），允许 clean_registry
    fixture 清空 _registry 后，下次 ModelProvider() 调用时重新注册。

    直接写入 _registry 而非调 ModelProvider.register()，因为 register()
    对重名抛 ValueError，而本函数的语义是"确保已注册"（idempotent ensure）。
    """
    for name, adapter_cls in _BUILTIN_ADAPTERS.items():
        if name not in ModelProvider._registry:
            ModelProvider._registry[name] = adapter_cls
