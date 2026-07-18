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
