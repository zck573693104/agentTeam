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
