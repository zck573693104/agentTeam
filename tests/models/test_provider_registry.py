"""ModelProvider registry 与 BaseAdapter ABC 行为测试。"""
import pytest


def test_base_adapter_default_build_usable():
    """BaseAdapter 提供默认 build() 实现:子类设类属性即可实例化,无需重写 build。"""
    from agentteam.models.adapters.base import BaseAdapter

    class MinimalAdapter(BaseAdapter):
        provider_name = "fake"
        env_var = "FAKE_API_KEY"
        chat_class_path = "some.module.FakeClass"

    adapter = MinimalAdapter({"k": "v"})
    assert adapter._api_keys == {"k": "v"}
    assert adapter.provider_name == "fake"


def test_base_adapter_complete_subclass_instantiable():
    """实现 build 的子类可正常实例化，且 __init__ 存储 api_keys。"""
    from agentteam.models.adapters.base import BaseAdapter

    class FakeAdapter(BaseAdapter):
        def build(self, ref):
            return None  # 测试用，不构造真实 LLM

    adapter = FakeAdapter({"k": "v"})
    assert adapter._api_keys == {"k": "v"}


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


# ===== Task 4: 内置 adapter 继承 BaseAdapter + 自动注册 =====


def test_builtin_providers_registered_by_default(clean_registry):
    """ModelProvider() 构造后 4 个内置 provider 自动注册。

    使用 clean_registry：先清空 _registry（模拟被前序测试污染），
    再构造 ModelProvider() 验证 register_builtins() 重新注册。
    这验证了 register_builtins() 的幂等性 + 测试隔离保证。
    """
    from agentteam.models.provider import ModelProvider

    # clean_registry 已清空 _registry，构造应触发 register_builtins() 重新注册
    ModelProvider()
    providers = ModelProvider.list_providers()
    for expected in ("qwen", "openai", "anthropic", "ollama"):
        assert expected in providers, f"内置 provider {expected!r} 未注册"
