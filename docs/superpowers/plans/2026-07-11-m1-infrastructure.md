# AgentTeam M1: 基础设施层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建 AgentTeam 框架的基础设施层——项目骨架、多供应商模型抽象（ModelProvider + 4 个适配器）、工具注册表与原生文件技能、SQLite 持久化存储，全部用 TDD 覆盖。

**Architecture:** 分层架构的最底层（基础设施层）。ModelProvider 用懒加载适配器模式按 `ModelRef` 解析出 LangChain `BaseChatModel`；ToolRegistry 统一注册 `@tool` 原生技能；Storage 用 stdlib `sqlite3` 初始化 schema 并提供 Run/Audit 仓储。所有外部依赖（langchain 社区包）懒加载，缺失时给出清晰错误，单测用 monkeypatch 注入假类，无需网络/密钥。

**Tech Stack:** Python ≥3.10、langchain-core、langchain-{openai,anthropic,ollama}、langchain-community(dashscope)、pydantic、sqlite3(stdlib)、pytest + pytest-asyncio。

**Spec reference:** `docs/superpowers/specs/2026-07-11-agent-team-design.md`（§5 工具层、§6 模型层、§7 持久化、§12 项目结构、§15 依赖）

**后续计划：** M2 领域与编译（Team/Worker/TeamCompiler/LangGraph）、M3 审批与轨迹、M4 MCP、M5 API+Web UI、M6 示例团队。`search_web` 技能因需选定搜索后端，推迟到 M5 实现。

---

## 文件结构

本计划创建/修改的文件及其职责：

| 文件 | 职责 |
|---|---|
| `pyproject.toml` | 项目元数据、依赖、pytest 配置 |
| `README.md` | 项目简介与快速开始 |
| `agentteam/__init__.py` | 包入口，导出 `__version__` |
| `agentteam/models/__init__.py` | 模型层包 |
| `agentteam/models/provider.py` | `ModelRef` 数据类 + `ModelProvider` 分发器 |
| `agentteam/models/adapters/__init__.py` | 适配器包 |
| `agentteam/models/adapters/qwen.py` | `QwenAdapter`（ChatTongyi，懒加载） |
| `agentteam/models/adapters/openai_adapter.py` | `OpenAIAdapter`（ChatOpenAI） |
| `agentteam/models/adapters/anthropic.py` | `AnthropicAdapter`（ChatAnthropic） |
| `agentteam/models/adapters/ollama.py` | `OllamaAdapter`（ChatOllama） |
| `agentteam/tools/__init__.py` | 工具层包 |
| `agentteam/tools/registry.py` | `ToolRegistry` 注册表 |
| `agentteam/tools/skills/__init__.py` | 技能包 + `register_builtin_skills` |
| `agentteam/tools/skills/file_ops.py` | `read_file`/`write_file`/`list_dir` 技能 |
| `agentteam/storage/__init__.py` | 存储层包 |
| `agentteam/storage/db.py` | `init_db` + schema |
| `agentteam/storage/runs.py` | `RunRepo`（runs 表 CRUD） |
| `agentteam/storage/audit.py` | `AuditRepo`（run_events/approvals 表） |
| `tests/conftest.py` | 共享 fixtures（tmp db） |
| `tests/models/test_provider.py` | ModelRef/ModelProvider 测试 |
| `tests/models/adapters/test_qwen.py` | Qwen 适配器测试 |
| `tests/models/adapters/test_openai.py` | OpenAI 适配器测试 |
| `tests/models/adapters/test_anthropic.py` | Anthropic 适配器测试 |
| `tests/models/adapters/test_ollama.py` | Ollama 适配器测试 |
| `tests/tools/test_registry.py` | ToolRegistry 测试 |
| `tests/tools/skills/test_file_ops.py` | 文件技能测试 |
| `tests/storage/test_db.py` | DB 初始化测试 |
| `tests/storage/test_runs.py` | RunRepo 测试 |
| `tests/storage/test_audit.py` | AuditRepo 测试 |

---

## Task 1: 项目骨架与 pyproject

**Files:**
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `agentteam/__init__.py`
- Create: `agentteam/models/__init__.py`
- Create: `agentteam/models/adapters/__init__.py`
- Create: `agentteam/tools/__init__.py`
- Create: `agentteam/tools/skills/__init__.py`
- Create: `agentteam/storage/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Test: `tests/test_smoke.py`

- [ ] **Step 1: 写 pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "agentteam"
version = "0.1.0"
description = "本地多智能体协作框架（迷你 AgentTeams）"
readme = "README.md"
requires-python = ">=3.10"
dependencies = [
    "langchain-core>=0.3",
    "pydantic>=2",
]

[project.optional-dependencies]
qwen = ["dashscope>=1.17", "langchain-community>=0.3"]
openai = ["langchain-openai>=0.2"]
anthropic = ["langchain-anthropic>=0.2"]
ollama = ["langchain-ollama>=0.2"]
dev = ["pytest>=8", "pytest-asyncio>=0.23"]

[tool.setuptools.packages.find]
include = ["agentteam*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 2: 写包入口 `agentteam/__init__.py`**

```python
"""AgentTeam —— 本地多智能体协作框架。"""

__version__ = "0.1.0"
```

- [ ] **Step 3: 写各子包 `__init__.py`（空占位）**

`agentteam/models/__init__.py`、`agentteam/models/adapters/__init__.py`、`agentteam/tools/__init__.py`、`agentteam/tools/skills/__init__.py`、`agentteam/storage/__init__.py`、`tests/__init__.py` 内容均为空字符串（空文件）。

- [ ] **Step 4: 写 conftest 与冒烟测试**

`tests/conftest.py`：

```python
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def tmp_db(tmp_path: Path) -> sqlite3.Connection:
    """提供一个临时 SQLite 连接，测试结束自动关闭。"""
    from agentteam.storage.db import init_db

    conn = init_db(tmp_path / "test.db")
    yield conn
    conn.close()
```

`tests/test_smoke.py`：

```python
def test_package_importable():
    import agentteam
    assert agentteam.__version__ == "0.1.0"
```

- [ ] **Step 5: 写 README.md**

```markdown
# AgentTeam

本地多智能体协作框架（迷你 AgentTeams），基于 Python + LangGraph。

## 安装

```bash
pip install -e ".[qwen,dev]"
```

## 状态

M1 基础设施层开发中。
```

- [ ] **Step 6: 安装依赖并跑冒烟测试**

Run: `pip install -e ".[dev]"`
Run: `pytest tests/test_smoke.py -v`
Expected: PASS（1 passed）

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml README.md agentteam/ tests/
git commit -m "chore: scaffold project skeleton and pyproject"
```

---

## Task 2: ModelRef 与 ModelProvider 基类

**Files:**
- Create: `agentteam/models/provider.py`
- Test: `tests/models/__init__.py`、`tests/models/test_provider.py`

- [ ] **Step 1: 写失败测试 `tests/models/test_provider.py`**

```python
import pytest

from agentteam.models.provider import ModelRef, ModelProvider


def test_model_ref_defaults():
    ref = ModelRef(provider="qwen", name="qwen-max")
    assert ref.provider == "qwen"
    assert ref.name == "qwen-max"
    assert ref.temperature == 0.7
    assert ref.streaming is True


def test_model_ref_is_frozen():
    ref = ModelRef(provider="openai", name="gpt-4o")
    with pytest.raises(Exception):
        ref.provider = "anthropic"  # type: ignore[misc]


def test_provider_unknown_raises():
    provider = ModelProvider()
    ref = ModelRef(provider="qwen", name="qwen-max")  # provider 合法但未注入适配器时由分发处理
    # 用一个非法 provider 值绕过 Literal 检查
    bad = ModelRef(provider="qwen", name="x")
    object.__setattr__(bad, "provider", "unknown")
    with pytest.raises(ValueError, match="Unknown provider"):
        provider.get_llm(bad)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/models/test_provider.py -v`
Expected: FAIL（`ModuleNotFoundError: agentteam.models.provider`）

- [ ] **Step 3: 写实现 `agentteam/models/provider.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

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
    """

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

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/models/test_provider.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add agentteam/models/provider.py tests/models/
git commit -m "feat(models): add ModelRef and ModelProvider base"
```

---

## Task 3: QwenAdapter（默认适配器）

**Files:**
- Create: `agentteam/models/adapters/qwen.py`
- Test: `tests/models/adapters/__init__.py`、`tests/models/adapters/test_qwen.py`

- [ ] **Step 1: 写失败测试 `tests/models/adapters/test_qwen.py`**

```python
import pytest

from agentteam.models.provider import ModelRef


def test_qwen_adapter_builds_with_explicit_key(monkeypatch):
    import agentteam.models.adapters.qwen as qwen

    captured = {}

    class FakeChatTongyi:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(qwen, "_load_chat_class", lambda: FakeChatTongyi)

    adapter = qwen.QwenAdapter({"dashscope": "fake-key"})
    ref = ModelRef(provider="qwen", name="qwen-max", temperature=0.5, streaming=False)
    llm = adapter.build(ref)

    assert isinstance(llm, FakeChatTongyi)
    assert captured["model"] == "qwen-max"
    assert captured["temperature"] == 0.5
    assert captured["dashscope_api_key"] == "fake-key"
    assert captured["streaming"] is False


def test_qwen_adapter_uses_env_key(monkeypatch):
    import agentteam.models.adapters.qwen as qwen

    monkeypatch.setenv("DASHSCOPE_API_KEY", "env-key")
    monkeypatch.setattr(qwen, "_load_chat_class", lambda: type("Fake", (), {"__init__": lambda self, **k: None}))

    adapter = qwen.QwenAdapter({})
    ref = ModelRef(provider="qwen", name="qwen-max")
    adapter.build(ref)  # 不应抛错


def test_qwen_adapter_missing_key_raises(monkeypatch):
    import agentteam.models.adapters.qwen as qwen

    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setattr(qwen, "_load_chat_class", lambda: type("Fake", (), {"__init__": lambda self, **k: None}))

    adapter = qwen.QwenAdapter({})
    with pytest.raises(ValueError, match="DASHSCOPE_API_KEY"):
        adapter.build(ModelRef(provider="qwen", name="qwen-max"))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/models/adapters/test_qwen.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 写实现 `agentteam/models/adapters/qwen.py`**

```python
from __future__ import annotations

import os
from typing import Callable

from langchain_core.language_models import BaseChatModel

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


class QwenAdapter:
    def __init__(self, api_keys: dict[str, str]) -> None:
        self._api_keys = api_keys

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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/models/adapters/test_qwen.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add agentteam/models/adapters/qwen.py tests/models/adapters/
git commit -m "feat(models): add QwenAdapter with lazy ChatTongyi loading"
```

---

## Task 4: OpenAIAdapter

**Files:**
- Create: `agentteam/models/adapters/openai_adapter.py`
- Test: `tests/models/adapters/test_openai.py`

- [ ] **Step 1: 写失败测试 `tests/models/adapters/test_openai.py`**

```python
import pytest

from agentteam.models.provider import ModelRef


def test_openai_adapter_builds(monkeypatch):
    import agentteam.models.adapters.openai_adapter as mod

    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(mod, "_load_chat_class", lambda: FakeChatOpenAI)

    adapter = mod.OpenAIAdapter({"openai": "sk-fake"})
    ref = ModelRef(provider="openai", name="gpt-4o", temperature=0.2, streaming=True)
    llm = adapter.build(ref)

    assert isinstance(llm, FakeChatOpenAI)
    assert captured["model"] == "gpt-4o"
    assert captured["temperature"] == 0.2
    assert captured["api_key"] == "sk-fake"
    assert captured["streaming"] is True


def test_openai_adapter_env_key(monkeypatch):
    import agentteam.models.adapters.openai_adapter as mod

    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.setattr(mod, "_load_chat_class", lambda: type("F", (), {"__init__": lambda self, **k: None}))

    mod.OpenAIAdapter({}).build(ModelRef(provider="openai", name="gpt-4o"))


def test_openai_adapter_missing_key(monkeypatch):
    import agentteam.models.adapters.openai_adapter as mod

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(mod, "_load_chat_class", lambda: type("F", (), {"__init__": lambda self, **k: None}))

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        mod.OpenAIAdapter({}).build(ModelRef(provider="openai", name="gpt-4o"))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/models/adapters/test_openai.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 写实现 `agentteam/models/adapters/openai_adapter.py`**

```python
from __future__ import annotations

import os
from typing import Callable

from langchain_core.language_models import BaseChatModel

from ..provider import ModelRef


def _load_chat_class() -> Callable:
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "OpenAI 适配器需要安装 langchain-openai：pip install 'agentteam[openai]'"
        ) from e
    return ChatOpenAI


class OpenAIAdapter:
    def __init__(self, api_keys: dict[str, str]) -> None:
        self._api_keys = api_keys

    def build(self, ref: ModelRef) -> BaseChatModel:
        ChatOpenAI = _load_chat_class()
        api_key = self._api_keys.get("openai") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("未提供 OPENAI_API_KEY")
        return ChatOpenAI(
            model=ref.name,
            temperature=ref.temperature,
            api_key=api_key,
            streaming=ref.streaming,
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/models/adapters/test_openai.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add agentteam/models/adapters/openai_adapter.py tests/models/adapters/test_openai.py
git commit -m "feat(models): add OpenAIAdapter"
```

---

## Task 5: AnthropicAdapter

**Files:**
- Create: `agentteam/models/adapters/anthropic.py`
- Test: `tests/models/adapters/test_anthropic.py`

- [ ] **Step 1: 写失败测试 `tests/models/adapters/test_anthropic.py`**

```python
import pytest

from agentteam.models.provider import ModelRef


def test_anthropic_adapter_builds(monkeypatch):
    import agentteam.models.adapters.anthropic as mod

    captured = {}

    class FakeChatAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(mod, "_load_chat_class", lambda: FakeChatAnthropic)

    adapter = mod.AnthropicAdapter({"anthropic": "sk-ant-fake"})
    ref = ModelRef(provider="anthropic", name="claude-3-5-sonnet-20240620", temperature=0.3, streaming=False)
    llm = adapter.build(ref)

    assert isinstance(llm, FakeChatAnthropic)
    assert captured["model"] == "claude-3-5-sonnet-20240620"
    assert captured["temperature"] == 0.3
    assert captured["api_key"] == "sk-ant-fake"


def test_anthropic_adapter_missing_key(monkeypatch):
    import agentteam.models.adapters.anthropic as mod

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(mod, "_load_chat_class", lambda: type("F", (), {"__init__": lambda self, **k: None}))

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        mod.AnthropicAdapter({}).build(ModelRef(provider="anthropic", name="claude-3-5-sonnet-20240620"))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/models/adapters/test_anthropic.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 写实现 `agentteam/models/adapters/anthropic.py`**

```python
from __future__ import annotations

import os
from typing import Callable

from langchain_core.language_models import BaseChatModel

from ..provider import ModelRef


def _load_chat_class() -> Callable:
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "Anthropic 适配器需要安装 langchain-anthropic：pip install 'agentteam[anthropic]'"
        ) from e
    return ChatAnthropic


class AnthropicAdapter:
    def __init__(self, api_keys: dict[str, str]) -> None:
        self._api_keys = api_keys

    def build(self, ref: ModelRef) -> BaseChatModel:
        ChatAnthropic = _load_chat_class()
        api_key = self._api_keys.get("anthropic") or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("未提供 ANTHROPIC_API_KEY")
        return ChatAnthropic(
            model=ref.name,
            temperature=ref.temperature,
            api_key=api_key,
            streaming=ref.streaming,
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/models/adapters/test_anthropic.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add agentteam/models/adapters/anthropic.py tests/models/adapters/test_anthropic.py
git commit -m "feat(models): add AnthropicAdapter"
```

---

## Task 6: OllamaAdapter

**Files:**
- Create: `agentteam/models/adapters/ollama.py`
- Test: `tests/models/adapters/test_ollama.py`

- [ ] **Step 1: 写失败测试 `tests/models/adapters/test_ollama.py`**

```python
from agentteam.models.provider import ModelRef


def test_ollama_adapter_builds(monkeypatch):
    import agentteam.models.adapters.ollama as mod

    captured = {}

    class FakeChatOllama:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(mod, "_load_chat_class", lambda: FakeChatOllama)

    adapter = mod.OllamaAdapter({})
    ref = ModelRef(provider="ollama", name="llama3", temperature=0.8, streaming=False)
    llm = adapter.build(ref)

    assert isinstance(llm, FakeChatOllama)
    assert captured["model"] == "llama3"
    assert captured["temperature"] == 0.8
    # Ollama 本地运行，无需 api_key
    assert "api_key" not in captured


def test_ollama_adapter_custom_base_url(monkeypatch):
    import agentteam.models.adapters.ollama as mod

    captured = {}

    class FakeChatOllama:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(mod, "_load_chat_class", lambda: FakeChatOllama)

    adapter = mod.OllamaAdapter({"ollama_base_url": "http://host:11434"})
    adapter.build(ModelRef(provider="ollama", name="llama3"))

    assert captured["base_url"] == "http://host:11434"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/models/adapters/test_ollama.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 写实现 `agentteam/models/adapters/ollama.py`**

```python
from __future__ import annotations

import os
from typing import Callable

from langchain_core.language_models import BaseChatModel

from ..provider import ModelRef


def _load_chat_class() -> Callable:
    try:
        from langchain_ollama import ChatOllama
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "Ollama 适配器需要安装 langchain-ollama：pip install 'agentteam[ollama]'"
        ) from e
    return ChatOllama


class OllamaAdapter:
    def __init__(self, api_keys: dict[str, str]) -> None:
        # 复用 api_keys dict 承载 ollama_base_url 等本地配置
        self._config = api_keys

    def build(self, ref: ModelRef) -> BaseChatModel:
        ChatOllama = _load_chat_class()
        base_url = self._config.get("ollama_base_url") or os.environ.get("OLLAMA_BASE_URL")
        kwargs: dict = {
            "model": ref.name,
            "temperature": ref.temperature,
        }
        if base_url:
            kwargs["base_url"] = base_url
        return ChatOllama(**kwargs)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/models/adapters/test_ollama.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add agentteam/models/adapters/ollama.py tests/models/adapters/test_ollama.py
git commit -m "feat(models): add OllamaAdapter"
```

---

## Task 7: ModelProvider 分发集成测试

**Files:**
- Test: `tests/models/test_provider.py`（追加）

- [ ] **Step 1: 追加分发测试到 `tests/models/test_provider.py`**

在文件末尾追加：

```python
def test_provider_dispatches_to_qwen(monkeypatch):
    import agentteam.models.adapters.qwen as qwen

    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    monkeypatch.setattr(qwen, "_load_chat_class", lambda: type("F", (), {"__init__": lambda self, **k: None}))

    llm = ModelProvider().get_llm(ModelRef(provider="qwen", name="qwen-max"))
    assert llm is not None


def test_provider_dispatches_to_openai(monkeypatch):
    import agentteam.models.adapters.openai_adapter as mod

    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setattr(mod, "_load_chat_class", lambda: type("F", (), {"__init__": lambda self, **k: None}))

    llm = ModelProvider().get_llm(ModelRef(provider="openai", name="gpt-4o"))
    assert llm is not None
```

- [ ] **Step 2: 跑测试确认通过**

Run: `pytest tests/models/ -v`
Expected: PASS（全部通过，含新增 2 个）

- [ ] **Step 3: Commit**

```bash
git add tests/models/test_provider.py
git commit -m "test(models): add ModelProvider dispatch integration tests"
```

---

## Task 8: ToolRegistry

**Files:**
- Create: `agentteam/tools/registry.py`
- Test: `tests/tools/__init__.py`、`tests/tools/test_registry.py`

- [ ] **Step 1: 写失败测试 `tests/tools/test_registry.py`**

```python
import pytest
from langchain_core.tools import StructuredTool


def _make_tool(name: str) -> StructuredTool:
    return StructuredTool.from_function(name=name, description=f"tool {name}", func=lambda: name)


def test_register_and_get():
    from agentteam.tools.registry import ToolRegistry

    reg = ToolRegistry()
    t = _make_tool("foo")
    reg.register(t)
    assert reg.get_tools(["foo"]) == [t]


def test_register_duplicate_raises():
    from agentteam.tools.registry import ToolRegistry

    reg = ToolRegistry()
    reg.register(_make_tool("foo"))
    with pytest.raises(ValueError, match="already registered"):
        reg.register(_make_tool("foo"))


def test_get_missing_raises():
    from agentteam.tools.registry import ToolRegistry

    reg = ToolRegistry()
    with pytest.raises(KeyError, match="not found"):
        reg.get_tools(["nope"])


def test_list_names():
    from agentteam.tools.registry import ToolRegistry

    reg = ToolRegistry()
    reg.register(_make_tool("a"))
    reg.register(_make_tool("b"))
    assert set(reg.list_names()) == {"a", "b"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/tools/test_registry.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 写实现 `agentteam/tools/registry.py`**

```python
from __future__ import annotations

from langchain_core.tools import BaseTool


class ToolRegistry:
    """工具统一注册表。Worker 配置里按名字引用工具，运行时取出绑定到 LLM。"""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get_tools(self, names: list[str]) -> list[BaseTool]:
        missing = [n for n in names if n not in self._tools]
        if missing:
            raise KeyError(f"Tools not found: {missing}")
        return [self._tools[n] for n in names]

    def list_names(self) -> list[str]:
        return list(self._tools.keys())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/tools/test_registry.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add agentteam/tools/registry.py tests/tools/__init__.py tests/tools/test_registry.py
git commit -m "feat(tools): add ToolRegistry"
```

---

## Task 9: 文件技能 read_file / write_file / list_dir

**Files:**
- Create: `agentteam/tools/skills/file_ops.py`
- Test: `tests/tools/skills/__init__.py`、`tests/tools/skills/test_file_ops.py`

- [ ] **Step 1: 写失败测试 `tests/tools/skills/test_file_ops.py`**

```python
from agentteam.tools.skills.file_ops import list_dir, read_file, write_file


def test_read_file(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello world", encoding="utf-8")
    assert read_file.invoke({"path": str(f)}) == "hello world"


def test_write_file_creates(tmp_path):
    f = tmp_path / "out.txt"
    result = write_file.invoke({"path": str(f), "content": "abc"})
    assert f.read_text(encoding="utf-8") == "abc"
    assert "3 characters" in result


def test_write_file_overwrites(tmp_path):
    f = tmp_path / "out.txt"
    f.write_text("old", encoding="utf-8")
    write_file.invoke({"path": str(f), "content": "new"})
    assert f.read_text(encoding="utf-8") == "new"


def test_list_dir(tmp_path):
    (tmp_path / "b.txt").write_text("x")
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    names = list_dir.invoke({"path": str(tmp_path)}).split("\n")
    assert names == ["a.txt", "b.txt", "sub"]


def test_read_file_missing_raises(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        read_file.invoke({"path": str(tmp_path / "nope.txt")})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/tools/skills/test_file_ops.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 写实现 `agentteam/tools/skills/file_ops.py`**

```python
from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool


@tool
def read_file(path: str) -> str:
    """读取指定路径文本文件的内容。"""
    return Path(path).read_text(encoding="utf-8")


@tool
def write_file(path: str, content: str) -> str:
    """将 content 写入指定路径的文件，已存在则覆盖。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} characters to {path}"


@tool
def list_dir(path: str) -> str:
    """列出目录下的条目，按名字排序，换行分隔。"""
    entries = sorted(p.name for p in Path(path).iterdir())
    return "\n".join(entries)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/tools/skills/test_file_ops.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add agentteam/tools/skills/file_ops.py tests/tools/skills/
git commit -m "feat(tools): add read_file/write_file/list_dir skills"
```

---

## Task 10: 内置技能注册 helper

**Files:**
- Modify: `agentteam/tools/skills/__init__.py`
- Test: `tests/tools/skills/test_builtin.py`

- [ ] **Step 1: 写失败测试 `tests/tools/skills/test_builtin.py`**

```python
def test_register_builtin_skills():
    from agentteam.tools.registry import ToolRegistry
    from agentteam.tools.skills import register_builtin_skills

    reg = ToolRegistry()
    register_builtin_skills(reg)

    names = set(reg.list_names())
    assert {"read_file", "write_file", "list_dir"}.issubset(names)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/tools/skills/test_builtin.py -v`
Expected: FAIL（`ImportError: cannot import name 'register_builtin_skills'`）

- [ ] **Step 3: 写实现 `agentteam/tools/skills/__init__.py`**

```python
from __future__ import annotations

from agentteam.tools.registry import ToolRegistry
from agentteam.tools.skills.file_ops import list_dir, read_file, write_file

_BUILTIN_TOOLS = [read_file, write_file, list_dir]


def register_builtin_skills(registry: ToolRegistry) -> None:
    """把内置原生技能注册到 registry。"""
    for t in _BUILTIN_TOOLS:
        registry.register(t)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/tools/skills/ -v`
Expected: PASS（全部通过）

- [ ] **Step 5: Commit**

```bash
git add agentteam/tools/skills/__init__.py tests/tools/skills/test_builtin.py
git commit -m "feat(tools): add register_builtin_skills helper"
```

---

## Task 11: SQLite 初始化与 schema

**Files:**
- Create: `agentteam/storage/db.py`
- Test: `tests/storage/__init__.py`、`tests/storage/test_db.py`

- [ ] **Step 1: 写失败测试 `tests/storage/test_db.py`**

```python
import sqlite3


def test_init_db_creates_tables(tmp_db: sqlite3.Connection):
    cur = tmp_db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cur.fetchall()}
    assert {"runs", "run_events", "approvals"}.issubset(tables)


def test_init_db_idempotent(tmp_db: sqlite3.Connection):
    # 再跑一次 schema 不应报错
    from agentteam.storage.db import SCHEMA

    tmp_db.executescript(SCHEMA)
    tmp_db.commit()


def test_init_db_creates_parent_dir(tmp_path):
    from agentteam.storage.db import init_db

    nested = tmp_path / "nested" / "deep" / "test.db"
    conn = init_db(nested)
    assert nested.exists()
    conn.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/storage/test_db.py -v`
Expected: FAIL（`ModuleNotFoundError`，且 conftest 的 tmp_db fixture 也会失败）

- [ ] **Step 3: 写实现 `agentteam/storage/db.py`**

```python
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id           TEXT PRIMARY KEY,
    team_name    TEXT NOT NULL,
    task         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    ended_at     TEXT,
    total_tokens INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS run_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    actor       TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}',
    duration_ms INTEGER,
    tokens      INTEGER,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_run_events_run_id ON run_events(run_id);

CREATE TABLE IF NOT EXISTS approvals (
    id            TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    requested_at  TEXT NOT NULL,
    decided_at    TEXT,
    decider       TEXT,
    reason        TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);
"""


def init_db(path: str | Path = "data/agentteam.db") -> sqlite3.Connection:
    """初始化 SQLite 数据库，创建 schema，返回连接。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/storage/test_db.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add agentteam/storage/db.py tests/storage/__init__.py tests/storage/test_db.py
git commit -m "feat(storage): add SQLite init and schema"
```

---

## Task 12: RunRepo（runs 表 CRUD）

**Files:**
- Create: `agentteam/storage/runs.py`
- Test: `tests/storage/test_runs.py`

- [ ] **Step 1: 写失败测试 `tests/storage/test_runs.py`**

```python
import sqlite3
from datetime import datetime, timezone

import pytest


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_create_and_get_run(tmp_db: sqlite3.Connection):
    from agentteam.storage.runs import RunRepo

    repo = RunRepo(tmp_db)
    run_id = repo.create_run(team_name="dev_team", task="写个 hello world")
    run = repo.get_run(run_id)
    assert run["team_name"] == "dev_team"
    assert run["task"] == "写个 hello world"
    assert run["status"] == "pending"
    assert run["total_tokens"] == 0


def test_update_status(tmp_db: sqlite3.Connection):
    from agentteam.storage.runs import RunRepo

    repo = RunRepo(tmp_db)
    run_id = repo.create_run(team_name="t", task="x")
    repo.update_status(run_id, "running")
    assert repo.get_run(run_id)["status"] == "running"


def test_end_run_sets_ended_at_and_tokens(tmp_db: sqlite3.Connection):
    from agentteam.storage.runs import RunRepo

    repo = RunRepo(tmp_db)
    run_id = repo.create_run(team_name="t", task="x")
    repo.end_run(run_id, status="completed", total_tokens=1234)
    run = repo.get_run(run_id)
    assert run["status"] == "completed"
    assert run["ended_at"] is not None
    assert run["total_tokens"] == 1234


def test_list_runs(tmp_db: sqlite3.Connection):
    from agentteam.storage.runs import RunRepo

    repo = RunRepo(tmp_db)
    a = repo.create_run(team_name="t", task="1")
    b = repo.create_run(team_name="t", task="2")
    runs = repo.list_runs()
    assert len(runs) == 2
    assert {r["id"] for r in runs} == {a, b}


def test_get_missing_run_returns_none(tmp_db: sqlite3.Connection):
    from agentteam.storage.runs import RunRepo

    repo = RunRepo(tmp_db)
    assert repo.get_run("nonexistent") is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/storage/test_runs.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 写实现 `agentteam/storage/runs.py`**

```python
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunRepo:
    """runs 表的读写。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create_run(self, team_name: str, task: str) -> str:
        run_id = uuid.uuid4().hex
        now = _now()
        self._conn.execute(
            "INSERT INTO runs (id, team_name, task, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'pending', ?, ?)",
            (run_id, team_name, task, now, now),
        )
        self._conn.commit()
        return run_id

    def get_run(self, run_id: str) -> sqlite3.Row | None:
        cur = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
        return cur.fetchone()

    def update_status(self, run_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now(), run_id),
        )
        self._conn.commit()

    def end_run(self, run_id: str, status: str, total_tokens: int = 0) -> None:
        now = _now()
        self._conn.execute(
            "UPDATE runs SET status = ?, ended_at = ?, updated_at = ?, total_tokens = ? "
            "WHERE id = ?",
            (status, now, now, total_tokens, run_id),
        )
        self._conn.commit()

    def list_runs(self) -> list[sqlite3.Row]:
        cur = self._conn.execute("SELECT * FROM runs ORDER BY created_at DESC")
        return cur.fetchall()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/storage/test_runs.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add agentteam/storage/runs.py tests/storage/test_runs.py
git commit -m "feat(storage): add RunRepo for runs table CRUD"
```

---

## Task 13: AuditRepo（run_events + approvals）

**Files:**
- Create: `agentteam/storage/audit.py`
- Test: `tests/storage/test_audit.py`

- [ ] **Step 1: 写失败测试 `tests/storage/test_audit.py`**

```python
import json
import sqlite3


def _seed_run(conn: sqlite3.Connection) -> str:
    from agentteam.storage.runs import RunRepo

    return RunRepo(conn).create_run(team_name="t", task="x")


def test_add_and_list_events(tmp_db: sqlite3.Connection):
    from agentteam.storage.audit import AuditRepo

    run_id = _seed_run(tmp_db)
    repo = AuditRepo(tmp_db)
    repo.add_event(run_id, event_type="run_start", actor="system", payload={"task": "x"})
    repo.add_event(run_id, event_type="worker_start", actor="coder", payload={"step": 0}, tokens=12)

    events = repo.list_events(run_id)
    assert len(events) == 2
    assert events[0]["event_type"] == "run_start"
    assert events[1]["tokens"] == 12
    assert json.loads(events[1]["payload"]) == {"step": 0}


def test_add_approval_and_decide(tmp_db: sqlite3.Connection):
    from agentteam.storage.audit import AuditRepo

    run_id = _seed_run(tmp_db)
    repo = AuditRepo(tmp_db)
    aid = repo.add_approval(run_id)
    assert repo.get_approval(aid)["status"] == "pending"

    repo.decide_approval(aid, decision="approved", decider="alice", reason="ok")
    ap = repo.get_approval(aid)
    assert ap["status"] == "approved"
    assert ap["decider"] == "alice"
    assert ap["decided_at"] is not None


def test_list_pending_approvals(tmp_db: sqlite3.Connection):
    from agentteam.storage.audit import AuditRepo

    run_id = _seed_run(tmp_db)
    repo = AuditRepo(tmp_db)
    a = repo.add_approval(run_id)
    repo.add_approval(run_id)  # 第二个保持 pending
    repo.decide_approval(a, "approved", "u")

    pending = repo.list_pending_approvals(run_id)
    assert len(pending) == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/storage/test_audit.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 写实现 `agentteam/storage/audit.py`**

```python
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditRepo:
    """run_events 与 approvals 表的读写，对标 AgentLoop 执行轨迹。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add_event(
        self,
        run_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        tokens: int | None = None,
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO run_events (run_id, event_type, actor, timestamp, payload, duration_ms, tokens) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                event_type,
                actor,
                _now(),
                json.dumps(payload or {}, ensure_ascii=False),
                duration_ms,
                tokens,
            ),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def list_events(self, run_id: str) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM run_events WHERE run_id = ? ORDER BY id ASC", (run_id,)
        )
        return cur.fetchall()

    def add_approval(self, run_id: str) -> str:
        approval_id = uuid.uuid4().hex
        self._conn.execute(
            "INSERT INTO approvals (id, run_id, status, requested_at) VALUES (?, ?, 'pending', ?)",
            (approval_id, run_id, _now()),
        )
        self._conn.commit()
        return approval_id

    def get_approval(self, approval_id: str) -> sqlite3.Row | None:
        cur = self._conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        return cur.fetchone()

    def decide_approval(
        self, approval_id: str, decision: str, decider: str, reason: str | None = None
    ) -> None:
        self._conn.execute(
            "UPDATE approvals SET status = ?, decided_at = ?, decider = ?, reason = ? WHERE id = ?",
            (decision, _now(), decider, reason, approval_id),
        )
        self._conn.commit()

    def list_pending_approvals(self, run_id: str) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM approvals WHERE run_id = ? AND status = 'pending'", (run_id,)
        )
        return cur.fetchall()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/storage/test_audit.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add agentteam/storage/audit.py tests/storage/test_audit.py
git commit -m "feat(storage): add AuditRepo for events and approvals"
```

---

## Task 14: 全量回归与 README 收尾

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 跑全量测试**

Run: `pytest -v`
Expected: 全部 PASS（约 27 个测试）

- [ ] **Step 2: 更新 README.md 反映 M1 完成**

```markdown
# AgentTeam

本地多智能体协作框架（迷你 AgentTeams），基于 Python + LangGraph。

## 安装

```bash
pip install -e ".[qwen,dev]"
```

## 模块

- `agentteam.models` —— 多供应商模型抽象（Qwen/OpenAI/Anthropic/Ollama）
- `agentteam.tools` —— ToolRegistry + 原生技能（read_file/write_file/list_dir）
- `agentteam.storage` —— SQLite 持久化（runs / run_events / approvals）

## 快速示例

```python
from agentteam.models import provider
from agentteam.tools.registry import ToolRegistry
from agentteam.tools.skills import register_builtin_skills
from agentteam.storage.db import init_db
from agentteam.storage.runs import RunRepo

# 模型
llm = provider.ModelProvider().get_llm(provider.ModelRef("qwen", "qwen-max"))

# 工具
reg = ToolRegistry()
register_builtin_skills(reg)
print(reg.list_names())  # ['read_file', 'write_file', 'list_dir']

# 存储
conn = init_db("data/agentteam.db")
run_id = RunRepo(conn).create_run("dev_team", "示例任务")
```

## 状态

- [x] M1 基础设施层
- [ ] M2 领域与编译（Team/Worker/TeamCompiler/LangGraph）
- [ ] M3 审批与轨迹
- [ ] M4 MCP 集成
- [ ] M5 API + Web UI
- [ ] M6 示例团队 + 测试
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: update README for M1 completion"
```

- [ ] **Step 4: 验证最终状态**

Run: `pytest -q && git log --oneline`
Expected: 全测试通过，约 14 个 commit

---

## 完成标准

M1 完成时应满足：
1. `pytest -q` 全绿（约 27 个测试）
2. `ModelProvider` 能按 `ModelRef` 分发到 4 个适配器（单测用 monkeypatch，无需真实密钥）
3. `ToolRegistry` 能注册并按名取工具，3 个文件技能可用
4. `init_db` 创建三张表，`RunRepo`/`AuditRepo` CRUD 正确
5. 代码全部提交到 git，commit 粒度清晰
