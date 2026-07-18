# SP6-P3 ModelProvider 注册表化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 class-level registry + BaseAdapter ABC 替代 `ModelProvider.get_llm` 中的 4 个 if/elif 硬编码，使新增 provider 无需修改框架源码（开闭原则），第三方 provider 可通过 `ModelProvider.register` 接入。

**Architecture:** 新建 `BaseAdapter` ABC（基于 `abc.ABC`/`abstractmethod`）定义 adapter 协议（`__init__(api_keys)` + `@abstractmethod build(ref) -> BaseChatModel`）。`ModelProvider` 持有 class-level `_registry: dict[str, type]`，通过 `@classmethod register(name, adapter_cls)` 注册（重名抛 ValueError），`list_providers()` 枚举。`get_llm` 从 registry 取 adapter_cls 实例化并调 `build`，未注册抛 ValueError 含已注册列表。4 个内置 adapter 继承 BaseAdapter，在 `adapters/__init__.py` import 时自动注册；`ModelProvider.__init__` 延迟 import adapters 包触发注册（避免循环依赖）。

**Tech Stack:** Python 3.11+, `abc.ABC`/`abc.abstractmethod`, pytest, langchain_core.BaseChatModel

---

## 文件结构

| 文件 | 责任 | 动作 |
|------|------|------|
| `agentteam/models/adapters/base.py` | BaseAdapter ABC（adapter 协议） | 新建 |
| `agentteam/models/provider.py` | ModelProvider + `_registry` + `register`/`list_providers` + `get_llm` | 修改 |
| `agentteam/models/adapters/__init__.py` | import + 自动注册 4 个内置 adapter | 修改（当前为空） |
| `agentteam/models/adapters/qwen.py` | QwenAdapter 继承 BaseAdapter | 修改 |
| `agentteam/models/adapters/openai_adapter.py` | OpenAIAdapter 继承 BaseAdapter | 修改 |
| `agentteam/models/adapters/anthropic.py` | AnthropicAdapter 继承 BaseAdapter | 修改 |
| `agentteam/models/adapters/ollama.py` | OllamaAdapter 继承 BaseAdapter（保留自定义 `__init__`） | 修改 |
| `tests/models/test_provider_registry.py` | registry 行为 + BaseAdapter ABC + get_llm 集成测试 | 新建 |
| `tests/models/test_provider.py` | 现有 ModelProvider 测试（Task 5 验证回归） | 可能修改 |

---

## Task 1: 创建 BaseAdapter ABC

**Files:**
- Create: `agentteam/models/adapters/base.py`
- Create: `tests/models/test_provider_registry.py`

- [ ] **Step 1: 写失败测试 — BaseAdapter ABC 强制子类实现 build**

创建 `d:\project\agentTeam\tests\models\test_provider_registry.py`:

```python
"""ModelProvider registry 与 BaseAdapter ABC 行为测试。"""
import pytest


def test_base_adapter_requires_build_method():
    """BaseAdapter 是 ABC：子类未实现 build 无法实例化。"""
    from agentteam.models.adapters.base import BaseAdapter

    class IncompleteAdapter(BaseAdapter):
        pass

    with pytest.raises(TypeError, match="abstract method"):
        IncompleteAdapter({"k": "v"})


def test_base_adapter_complete_subclass_instantiable():
    """实现 build 的子类可正常实例化，且 __init__ 存储 api_keys。"""
    from agentteam.models.adapters.base import BaseAdapter

    class FakeAdapter(BaseAdapter):
        def build(self, ref):
            return None  # 测试用，不构造真实 LLM

    adapter = FakeAdapter({"k": "v"})
    assert adapter._api_keys == {"k": "v"}
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/models/test_provider_registry.py -v`
Expected: 2 个测试 FAIL（`ModuleNotFoundError: No module named 'agentteam.models.adapters.base'`）

- [ ] **Step 3: 实现 — 创建 BaseAdapter ABC**

创建 `d:\project\agentTeam\agentteam\models\adapters\base.py`:

```python
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
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/models/test_provider_registry.py -v`
Expected: 2 PASS

- [ ] **Step 5: 提交**

```powershell
git add agentteam/models/adapters/base.py tests/models/test_provider_registry.py
git commit -m "feat(models): BaseAdapter ABC 定义 adapter 协议"
```

---

## Task 2: ModelProvider class-level registry + register/list_providers

**Files:**
- Modify: `agentteam/models/provider.py`（加 `_registry` + `register` + `list_providers`，`get_llm` 暂不动）
- Modify: `tests/models/test_provider_registry.py`（加 `clean_registry` fixture + 3 个 register 测试）

**说明：** 本 Task 仅加 registry 基础设施。`get_llm` 仍走 if/elif（Task 3 改）。
因此 Task 2 的 `test_register_custom_provider` 验证注册行为（adapter 入 registry、`list_providers` 可见），
`get_llm` 与 registry 的集成测试放在 Task 3（TDD：先实现 get_llm 改造再测集成）。

- [ ] **Step 1: 写失败测试 — register + list_providers 行为**

在 `d:\project\agentTeam\tests\models\test_provider_registry.py` 末尾追加 `clean_registry` fixture 与 3 个测试:

```python
# ===== Task 2: registry + register + list_providers =====


@pytest.fixture
def clean_registry():
    """每个测试前后清理 ModelProvider._registry，保证测试隔离。

    _registry 是 class-level 状态，若不清理会跨测试污染（如 fake provider 泄漏到后续测试）。
    策略：保存当前状态 → 清空 → 测试 → 恢复。
    pytest fixture 定义顺序不影响使用，可放在文件末尾。
    """
    from agentteam.models.provider import ModelProvider

    saved = dict(ModelProvider._registry)
    ModelProvider._registry.clear()
    yield ModelProvider._registry
    ModelProvider._registry.clear()
    ModelProvider._registry.update(saved)


def test_register_custom_provider(clean_registry):
    """register 把 adapter_cls 加入 registry，list_providers 能看到。"""
    from agentteam.models.adapters.base import BaseAdapter
    from agentteam.models.provider import ModelProvider

    class FakeAdapter(BaseAdapter):
        def build(self, ref):
            return None

    ModelProvider.register("fake_provider", FakeAdapter)
    assert "fake_provider" in ModelProvider.list_providers()
    assert ModelProvider._registry["fake_provider"] is FakeAdapter


def test_register_duplicate_raises(clean_registry):
    """重名注册抛 ValueError，防止意外覆盖。"""
    from agentteam.models.adapters.base import BaseAdapter
    from agentteam.models.provider import ModelProvider

    class FakeAdapter(BaseAdapter):
        def build(self, ref):
            return None

    ModelProvider.register("dup_provider", FakeAdapter)
    with pytest.raises(ValueError, match="already registered"):
        ModelProvider.register("dup_provider", FakeAdapter)


def test_list_providers_returns_all(clean_registry):
    """list_providers 返回所有已注册 provider name（与 _registry keys 一致）。"""
    from agentteam.models.adapters.base import BaseAdapter
    from agentteam.models.provider import ModelProvider

    class FakeA(BaseAdapter):
        def build(self, ref):
            return None

    class FakeB(BaseAdapter):
        def build(self, ref):
            return None

    ModelProvider.register("alpha", FakeA)
    ModelProvider.register("beta", FakeB)
    providers = ModelProvider.list_providers()
    assert set(providers) == {"alpha", "beta"}
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/models/test_provider_registry.py -v`
Expected: 新增 3 个测试 FAIL
- `test_register_custom_provider`: `AttributeError: type object 'ModelProvider' has no attribute '_registry'`
- `test_register_duplicate_raises`: 同上
- `test_list_providers_returns_all`: 同上
（Task 1 的 2 个 BaseAdapter 测试仍 PASS）

- [ ] **Step 3: 实现 — 给 ModelProvider 加 _registry + register + list_providers**

修改 `d:\project\agentTeam\agentteam\models\provider.py`。在 `class ModelProvider:` 类体内，`def __init__` 之前插入 class-level `_registry` 与两个 classmethod。完整文件如下:

```python
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

    def get_llm(self, ref: ModelRef) -> BaseChatModel:
        if ref.provider == "qwen":
            from .adapters.qwen import QwenAdapter

            return QwenAdapter(self._api_keys).build(ref)
        if ref.provider == "openai":
            from .adapters.openai_adapter import OpenAIAdapter

            return OpenAIAdapter(self._api_keys).build(ref)
        if ref.provider == "anthropic":
            from .adapters.anthropic import AnthropicAdapter

            return AnthropicAdapter(self._api_keys).build(ref)
        if ref.provider == "ollama":
            from .adapters.ollama import OllamaAdapter

            return OllamaAdapter(self._api_keys).build(ref)
        raise ValueError(f"Unknown provider: {ref.provider}")
```

注意：`get_llm` 此 Task 暂不动（仍用 if/elif），Task 3 再改为 registry 分派。

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/models/test_provider_registry.py -v`
Expected: 5 PASS（2 BaseAdapter + 3 register）

- [ ] **Step 5: 提交**

```powershell
git add agentteam/models/provider.py tests/models/test_provider_registry.py
git commit -m "feat(models): ModelProvider 加 _registry/register/list_providers"
```

---

## Task 3: 改 get_llm 用 registry 替代 if/elif

**Files:**
- Modify: `agentteam/models/provider.py`（`get_llm` 改用 registry）
- Modify: `tests/models/test_provider_registry.py`（加 get_llm 集成测试）

**注意：** 此 Task 后内置 adapter 尚未自动注册（Task 4 才做）。
现有 `tests/models/test_provider.py` 的 `test_provider_dispatches_to_qwen` / `test_provider_dispatches_to_openai` 会临时 FAIL（"qwen"/"openai" 未注册），`test_provider_unknown_raises` 仍 PASS（match="Unknown provider" 仍匹配）。此为预期回归，Task 4 自动注册后恢复。本 Task 直接提交（含临时回归）。

- [ ] **Step 1: 写失败测试 — get_llm 用 registry 解析 + 未知 provider 列已注册**

在 `d:\project\agentTeam\tests\models\test_provider_registry.py` 末尾追加:

```python
# ===== Task 3: get_llm 用 registry 替代 if/elif =====


def test_get_llm_resolves_registered_provider(clean_registry):
    """注册 fake adapter 后，get_llm 调用其 build 并返回结果。"""
    from agentteam.models.adapters.base import BaseAdapter
    from agentteam.models.provider import ModelProvider, ModelRef

    sentinel = object()  # 哨兵对象，验证 build 被调用且结果被透传

    class FakeAdapter(BaseAdapter):
        def build(self, ref):
            return sentinel

    ModelProvider.register("fake", FakeAdapter)
    ref = ModelRef(provider="fake", name="fake-model")
    llm = ModelProvider().get_llm(ref)
    assert llm is sentinel


def test_get_llm_unknown_provider_lists_registered(clean_registry):
    """未知 provider 抛 ValueError，错误信息含 'Registered:' 与已注册列表。"""
    from agentteam.models.adapters.base import BaseAdapter
    from agentteam.models.provider import ModelProvider, ModelRef

    class FakeAdapter(BaseAdapter):
        def build(self, ref):
            return None

    ModelProvider.register("alpha", FakeAdapter)
    ModelProvider.register("beta", FakeAdapter)

    # 用非法 provider 值绕过 Literal 检查
    bad = ModelRef(provider="qwen", name="x")
    object.__setattr__(bad, "provider", "unknown")
    with pytest.raises(ValueError) as exc_info:
        ModelProvider().get_llm(bad)
    msg = str(exc_info.value)
    assert "Unknown provider" in msg
    assert "Registered:" in msg
    assert "alpha" in msg
    assert "beta" in msg
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/models/test_provider_registry.py -v`
Expected: 新增 2 个测试 FAIL
- `test_get_llm_resolves_registered_provider`: FAIL（`get_llm` 仍走 if/elif，"fake" 未匹配 → 抛 `ValueError: Unknown provider: fake`，而非调用 build）
- `test_get_llm_unknown_provider_lists_registered`: FAIL（错误信息为 `"Unknown provider: unknown"`，无 `"Registered:"`）
（前 5 个测试仍 PASS）

- [ ] **Step 3: 实现 — get_llm 改用 registry**

修改 `d:\project\agentTeam\agentteam\models\provider.py` 的 `get_llm` 方法，替换整个 if/elif 链为 registry 查找:

把:
```python
    def get_llm(self, ref: ModelRef) -> BaseChatModel:
        if ref.provider == "qwen":
            from .adapters.qwen import QwenAdapter

            return QwenAdapter(self._api_keys).build(ref)
        if ref.provider == "openai":
            from .adapters.openai_adapter import OpenAIAdapter

            return OpenAIAdapter(self._api_keys).build(ref)
        if ref.provider == "anthropic":
            from .adapters.anthropic import AnthropicAdapter

            return AnthropicAdapter(self._api_keys).build(ref)
        if ref.provider == "ollama":
            from .adapters.ollama import OllamaAdapter

            return OllamaAdapter(self._api_keys).build(ref)
        raise ValueError(f"Unknown provider: {ref.provider}")
```

改为:
```python
    def get_llm(self, ref: ModelRef) -> BaseChatModel:
        adapter_cls = self._registry.get(ref.provider)
        if adapter_cls is None:
            raise ValueError(
                f"Unknown provider: {ref.provider}. "
                f"Registered: {self.list_providers()}"
            )
        return adapter_cls(self._api_keys).build(ref)
```

- [ ] **Step 4: 运行 registry 测试验证通过**

Run: `python -m pytest tests/models/test_provider_registry.py -v`
Expected: 7 PASS（2 BaseAdapter + 3 register + 2 get_llm）

- [ ] **Step 5: 确认现有 dispatch 测试临时 FAIL（预期回归）**

Run: `python -m pytest tests/models/test_provider.py -v`
Expected:
- `test_provider_unknown_raises`: PASS（`match="Unknown provider"` 仍匹配新错误信息）
- `test_model_ref_defaults`: PASS（不涉及 get_llm）
- `test_model_ref_is_frozen`: PASS（不涉及 get_llm）
- `test_provider_dispatches_to_qwen`: **FAIL**（registry 无 "qwen"，Task 4 修复）
- `test_provider_dispatches_to_openai`: **FAIL**（registry 无 "openai"，Task 4 修复）

此为预期回归，不在此 Task 修复，直接进入 Task 4。

- [ ] **Step 6: 提交（含临时回归）**

```powershell
git add agentteam/models/provider.py tests/models/test_provider_registry.py
git commit -m "refactor(models): get_llm 改用 registry 替代 if/elif 分派"
```

---

## Task 4: 内置 adapter 继承 BaseAdapter + 自动注册

**Files:**
- Modify: `agentteam/models/adapters/qwen.py`（继承 BaseAdapter，删除自定义 `__init__`）
- Modify: `agentteam/models/adapters/openai_adapter.py`（继承 BaseAdapter，删除自定义 `__init__`）
- Modify: `agentteam/models/adapters/anthropic.py`（继承 BaseAdapter，删除自定义 `__init__`）
- Modify: `agentteam/models/adapters/ollama.py`（继承 BaseAdapter，保留自定义 `__init__`）
- Modify: `agentteam/models/adapters/__init__.py`（import + 注册 4 个内置）
- Modify: `agentteam/models/provider.py`（`__init__` 延迟 import adapters 触发注册）
- Modify: `tests/models/test_provider_registry.py`（加 builtin 注册测试）

**循环依赖说明：** `adapters/__init__.py` import `ModelProvider` 不会循环，因为 `provider.py` 不在模块顶层 import adapters，只在 `ModelProvider.__init__` 方法内延迟 import。链路：`provider.py` 模块加载完成（定义 ModelRef/ModelProvider）→ 用户调 `ModelProvider()` → `__init__` 内 `from agentteam.models import adapters` → `adapters/__init__.py` import `ModelProvider`（已加载，无循环）→ 注册。

- [ ] **Step 1: 写失败测试 — 内置 provider 默认注册**

在 `d:\project\agentTeam\tests\models\test_provider_registry.py` 末尾追加（**不使用** `clean_registry`，验证真实默认状态）:

```python
# ===== Task 4: 内置 adapter 继承 BaseAdapter + 自动注册 =====


def test_builtin_providers_registered_by_default():
    """ModelProvider() 构造后 4 个内置 provider 自动注册。

    不使用 clean_registry：验证 import 副作用注册的默认状态。
    用 'in' 而非 '==' 断言，避免被其他测试残留的 fake provider 干扰。
    """
    from agentteam.models.provider import ModelProvider

    # 构造触发 adapters 包 import → 自动注册
    ModelProvider()
    providers = ModelProvider.list_providers()
    for expected in ("qwen", "openai", "anthropic", "ollama"):
        assert expected in providers, f"内置 provider {expected!r} 未注册"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/models/test_provider_registry.py::test_builtin_providers_registered_by_default -v`
Expected: FAIL（`AssertionError: 内置 provider 'qwen' 未注册`）

- [ ] **Step 3: 实现 — QwenAdapter 继承 BaseAdapter**

修改 `d:\project\agentTeam\agentteam\models\adapters\qwen.py`。

在顶部 import 区，把:
```python
from langchain_core.language_models import BaseChatModel

from ..provider import ModelRef
```
改为:
```python
from langchain_core.language_models import BaseChatModel

from ..provider import ModelRef
from .base import BaseAdapter
```

把类声明与 `__init__`:
```python
class QwenAdapter:
    def __init__(self, api_keys: dict[str, str]) -> None:
        self._api_keys = api_keys

    def build(self, ref: ModelRef) -> BaseChatModel:
```
改为（删除 `__init__`，复用 BaseAdapter 的）:
```python
class QwenAdapter(BaseAdapter):
    def build(self, ref: ModelRef) -> BaseChatModel:
```

`build` 方法体保持不变。

- [ ] **Step 4: 实现 — OpenAIAdapter 继承 BaseAdapter**

修改 `d:\project\agentTeam\agentteam\models\adapters\openai_adapter.py`。

在顶部 import 区，把:
```python
from langchain_core.language_models import BaseChatModel

from ..provider import ModelRef
```
改为:
```python
from langchain_core.language_models import BaseChatModel

from ..provider import ModelRef
from .base import BaseAdapter
```

把类声明与 `__init__`:
```python
class OpenAIAdapter:
    def __init__(self, api_keys: dict[str, str]) -> None:
        self._api_keys = api_keys

    def build(self, ref: ModelRef) -> BaseChatModel:
```
改为（删除 `__init__`，复用 BaseAdapter 的）:
```python
class OpenAIAdapter(BaseAdapter):
    def build(self, ref: ModelRef) -> BaseChatModel:
```

`build` 方法体保持不变。

- [ ] **Step 5: 实现 — AnthropicAdapter 继承 BaseAdapter**

修改 `d:\project\agentTeam\agentteam\models\adapters\anthropic.py`。

在顶部 import 区，把:
```python
from langchain_core.language_models import BaseChatModel

from ..provider import ModelRef
```
改为:
```python
from langchain_core.language_models import BaseChatModel

from ..provider import ModelRef
from .base import BaseAdapter
```

把类声明与 `__init__`:
```python
class AnthropicAdapter:
    def __init__(self, api_keys: dict[str, str]) -> None:
        self._api_keys = api_keys

    def build(self, ref: ModelRef) -> BaseChatModel:
```
改为（删除 `__init__`，复用 BaseAdapter 的）:
```python
class AnthropicAdapter(BaseAdapter):
    def build(self, ref: ModelRef) -> BaseChatModel:
```

`build` 方法体保持不变。

- [ ] **Step 6: 实现 — OllamaAdapter 继承 BaseAdapter（保留自定义 __init__）**

修改 `d:\project\agentTeam\agentteam\models\adapters\ollama.py`。

在顶部 import 区，把:
```python
from langchain_core.language_models import BaseChatModel

from ..provider import ModelRef
```
改为:
```python
from langchain_core.language_models import BaseChatModel

from ..provider import ModelRef
from .base import BaseAdapter
```

把类声明:
```python
class OllamaAdapter:
```
改为:
```python
class OllamaAdapter(BaseAdapter):
```

**保留** `OllamaAdapter.__init__`（它用 `self._config` 而非 `self._api_keys`，覆盖 BaseAdapter 的 `__init__` 是合法的）。`build` 方法体保持不变。修改后 OllamaAdapter 完整代码:

```python
from __future__ import annotations

import os
from typing import Callable

from langchain_core.language_models import BaseChatModel

from ..provider import ModelRef
from .base import BaseAdapter


def _load_chat_class() -> Callable:
    """懒加载 ChatOllama，缺失依赖时给清晰错误。"""
    try:
        from langchain_ollama import ChatOllama
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "Ollama 适配器需要安装 langchain-ollama：pip install 'agentteam[ollama]'"
        ) from e
    return ChatOllama


class OllamaAdapter(BaseAdapter):
    def __init__(self, api_keys: dict[str, str]) -> None:
        # 复用 api_keys dict 承载 ollama_base_url 等本地配置
        # 覆盖 BaseAdapter.__init__（用 _config 而非 _api_keys）
        self._config = api_keys

    def build(self, ref: ModelRef) -> BaseChatModel:
        ChatOllama = _load_chat_class()
        base_url = self._config.get("ollama_base_url") or os.environ.get("OLLAMA_BASE_URL")
        kwargs: dict[str, object] = {
            "model": ref.name,
            "temperature": ref.temperature,
            "streaming": ref.streaming,
        }
        if base_url:
            kwargs["base_url"] = base_url
        return ChatOllama(**kwargs)
```

- [ ] **Step 7: 实现 — adapters/__init__.py 自动注册 4 个内置**

修改 `d:\project\agentTeam\agentteam\models\adapters\__init__.py`（当前为空文件），写入:

```python
"""Model adapter 包 — import 时自动注册内置 adapter。

循环依赖说明：
- 本模块 import ModelProvider（来自 ..provider）
- provider.py 不在顶层 import 本包，只在 ModelProvider.__init__ 内延迟 import
- 因此无循环：provider.py 模块加载完成 → 后续 ModelProvider() 实例化触发本包 import → 注册

第三方 provider 可在自身模块 import 时调用 ModelProvider.register 接入。
"""
from agentteam.models.provider import ModelProvider

from .qwen import QwenAdapter
from .openai_adapter import OpenAIAdapter
from .anthropic import AnthropicAdapter
from .ollama import OllamaAdapter

# 框架启动时自动注册内置 adapter（import 副作用，仅首次 import 执行一次）
ModelProvider.register("qwen", QwenAdapter)
ModelProvider.register("openai", OpenAIAdapter)
ModelProvider.register("anthropic", AnthropicAdapter)
ModelProvider.register("ollama", OllamaAdapter)
```

- [ ] **Step 8: 实现 — ModelProvider.__init__ 延迟 import adapters 触发注册**

修改 `d:\project\agentTeam\agentteam\models\provider.py` 的 `__init__` 方法，加延迟 import。

把:
```python
    def __init__(self, api_keys: dict[str, str] | None = None) -> None:
        self._api_keys = api_keys or {}
```
改为:
```python
    def __init__(self, api_keys: dict[str, str] | None = None) -> None:
        self._api_keys = api_keys or {}
        # 延迟 import adapters 包，触发内置 adapter 自动注册（import 副作用）
        # 放在此处而非模块顶层，避免循环依赖（adapters/__init__.py 反向 import ModelProvider）
        from agentteam.models import adapters  # noqa: F401
```

- [ ] **Step 9: 运行 builtin 注册测试验证通过**

Run: `python -m pytest tests/models/test_provider_registry.py::test_builtin_providers_registered_by_default -v`
Expected: PASS

- [ ] **Step 10: 运行 registry 全套测试验证通过**

Run: `python -m pytest tests/models/test_provider_registry.py -v`
Expected: 8 PASS

说明：使用 `clean_registry` 的测试因 fixture 保存（含已注册内置）→ 清空 → 测试 → 恢复，内置 adapter 在恢复后仍注册。首次 `ModelProvider()` 调用触发 `adapters` import 后，内置 adapter 永久驻留 `_registry`（除非被 `clean_registry` 临时清空）。

- [ ] **Step 11: 运行现有 provider 测试验证回归修复**

Run: `python -m pytest tests/models/test_provider.py -v`
Expected: 全部 PASS（Task 3 的临时回归已修复 — "qwen"/"openai" 现已自动注册）

- [ ] **Step 12: 提交**

```powershell
git add agentteam/models/adapters/qwen.py agentteam/models/adapters/openai_adapter.py agentteam/models/adapters/anthropic.py agentteam/models/adapters/ollama.py agentteam/models/adapters/__init__.py agentteam/models/provider.py tests/models/test_provider_registry.py
git commit -m "feat(models): 内置 adapter 继承 BaseAdapter + 自动注册"
```

---

## Task 5: 全量回归 + Phase commit

**Files:**
- 可能修改: `tests/models/test_provider.py`（若 mock 方式需调整）
- 无源码修改

- [ ] **Step 1: 运行全套 models 测试**

Run: `python -m pytest tests/models/ -v`
Expected: 全部 PASS

说明：现有 `test_provider_dispatches_to_qwen` / `test_provider_dispatches_to_openai` 的 mock 方式（`monkeypatch.setattr(qwen, "_load_chat_class", ...)`）在 registry 改造后仍有效，因为：
- `get_llm` 从 registry 取 `QwenAdapter` → 实例化 → 调 `build`
- `QwenAdapter.build` 内部调模块级 `_load_chat_class()`（已被 monkeypatch 替换）
- 链路不变，mock 仍生效

若个别测试因 mock 依赖 if/elif 内部 import 路径而 FAIL，更新为直接 mock adapter 的 `_load_chat_class`（保持原 mock 方式）。

- [ ] **Step 2: 运行全量回归测试**

Run: `python -m pytest -q`
Expected: 全部 PASS（目标：原 418+ → 426+，新增 8 个 registry 测试）

- [ ] **Step 3: 检查工作树状态**

Run: `git status`
Expected: clean working tree（或仅有本 Task 的测试修复）

- [ ] **Step 4: 若有回归修复，提交**

若 Step 1/2 发现需调整的测试，提交:

```powershell
git add tests/models/test_provider.py
git commit -m "test(models): 适配 registry 改造后的 provider 测试"
```

（若无修改则跳过）

- [ ] **Step 5: Phase commit — P3 整合提交（如有未提交的整合修改）**

若前述提交已覆盖所有改动，跳过此步。否则整合提交:

```powershell
git add -A
git commit -m "refactor(models): P3 ModelProvider 注册表化，消除 if/elif 硬编码"
```

- [ ] **Step 6: 检查最近提交历史**

Run: `git log --oneline -8`
Expected: 看到 P3 的 4-5 个提交：
1. `feat(models): BaseAdapter ABC 定义 adapter 协议`
2. `feat(models): ModelProvider 加 _registry/register/list_providers`
3. `refactor(models): get_llm 改用 registry 替代 if/elif 分派`
4. `feat(models): 内置 adapter 继承 BaseAdapter + 自动注册`
5. （可选）`test(models): 适配 registry 改造后的 provider 测试`

---

## Self-Review

**1. Spec coverage（对照 spec §5.2-5.4）:**
- ✅ BaseAdapter ABC（`__init__(api_keys)` + `@abstractmethod build`）— Task 1
- ✅ `ModelProvider._registry: dict[str, type]` class-level — Task 2
- ✅ `@classmethod register(name, adapter_cls)` 重名抛 ValueError — Task 2
- ✅ `@classmethod list_providers() -> list[str]` — Task 2
- ✅ `get_llm` 用 registry 替代 if/elif，未注册抛 ValueError 含已注册列表 — Task 3
- ✅ 4 个内置 adapter 继承 BaseAdapter — Task 4
- ✅ `adapters/__init__.py` import 时自动注册 4 个内置 — Task 4
- ✅ `ModelProvider.__init__` 延迟 import adapters 触发注册 — Task 4
- ✅ `test_register_custom_provider` — Task 2（验证注册；get_llm 集成在 Task 3 测，TDD 原则）
- ✅ `test_register_duplicate_raises` — Task 2
- ✅ `test_list_providers_returns_all` — Task 2
- ✅ `test_get_llm_unknown_provider_lists_registered` — Task 3
- ✅ `test_builtin_providers_registered_by_default` — Task 4
- ✅ 全量回归 — Task 5
- ✅ Phase commit — Task 5

**2. Placeholder scan:**
- 无 "TBD"/"TODO"/"fill in"/"implement later"
- 每个 Step 含完整代码或完整命令
- 测试代码完整可运行，无省略
- 修改步骤含完整 before/after 代码块

**3. Type consistency:**
- `BaseAdapter.__init__(api_keys: dict[str, str])` — Task 1 定义，Task 4 内置 adapter 复用/覆盖，一致
- `BaseAdapter.build(ref: ModelRef) -> BaseChatModel` — Task 1 定义，所有 adapter 实现签名一致
- `ModelProvider._registry: dict[str, type]` — Task 2 定义，后续 Task 引用一致
- `ModelProvider.register(name: str, adapter_cls: type) -> None` — Task 2 定义，Task 4 调用 `ModelProvider.register("qwen", QwenAdapter)` 一致
- `ModelProvider.list_providers() -> list[str]` — Task 2 定义，Task 3 错误信息 `{self.list_providers()}` 引用一致
- `get_llm` 错误格式 `"Unknown provider: {x}. Registered: {list}"` — Task 3 实现与 Task 3 测试断言（"Unknown provider" + "Registered:" + provider names）一致
- `clean_registry` fixture：yield `ModelProvider._registry`，测试中通过 `ModelProvider._registry` / `ModelProvider.list_providers()` 访问 — 一致

**4. 循环依赖确认:**
- `provider.py` 顶层 import：`dataclasses`、`typing`、`langchain_core` — 不含 adapters
- `adapters/__init__.py` 顶层 import：`from agentteam.models.provider import ModelProvider`（provider.py 已加载完成）
- `adapters/base.py` 顶层 import：`from ..provider import ModelRef`（provider.py 已加载完成）
- `provider.py` 的 `__init__` 方法内延迟 `from agentteam.models import adapters`
- 链路：`provider.py` 模块加载（定义 ModelRef/ModelProvider） → 用户调 `ModelProvider()` → `__init__` import adapters → `adapters/__init__.py` import ModelProvider（已加载，无循环）→ import 4 个 adapter → register 4 次 → 完成
- Python `sys.modules` 缓存保证 `adapters/__init__.py` 仅执行一次（首次 import），后续 `ModelProvider()` 调用不重复注册

**5. 测试隔离确认:**
- `clean_registry` fixture：save → clear → yield → clear → restore，保证 fake provider 不泄漏
- `test_builtin_providers_registered_by_default` 不用 `clean_registry`，用 `in` 而非 `==` 断言，容忍其他测试残留
- 测试执行顺序无关：无论 `clean_registry` 测试先跑还是 `test_builtin_providers_registered_by_default` 先跑，均正确

---

## 执行选择

Plan 已保存到 `docs/superpowers/plans/2026-07-18-sp6-p3-modelprovider-registry.md`。两种执行方式:

1. **Subagent-Driven（推荐）** — 每个任务派发新 subagent，任务间 review
2. **Inline Execution** — 在当前会话执行，批量 checkpoint review

选择哪种?
