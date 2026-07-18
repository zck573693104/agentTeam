"""Preset catalog 基础接口测试。"""
import pytest


def test_list_presets_returns_list():
    """list_presets() 返回 list(初始可为空)。"""
    from agentteam.presets import list_presets
    result = list_presets()
    assert isinstance(result, list)


def test_get_preset_nonexistent_raises_keyerror():
    """get_preset 不存在名字抛 KeyError,错误信息含可用列表。"""
    from agentteam.presets import get_preset
    with pytest.raises(KeyError) as exc_info:
        get_preset("nonexistent")
    assert "nonexistent" in str(exc_info.value)
    assert "Available" in str(exc_info.value)


def test_preset_registry_is_dict():
    """PRESET_REGISTRY 是 dict(初始可空,后续 task 填充)。"""
    from agentteam.presets import PRESET_REGISTRY
    assert isinstance(PRESET_REGISTRY, dict)


def test_list_presets_includes_enterprise_dev():
    """list_presets 包含 enterprise_dev 条目。"""
    from agentteam.presets import list_presets
    result = list_presets()
    names = [p["name"] for p in result]
    assert "enterprise_dev" in names


def test_get_preset_enterprise_dev_returns_module():
    """get_preset('enterprise_dev') 返回有效模块。"""
    from agentteam.presets import get_preset
    mod = get_preset("enterprise_dev")
    assert hasattr(mod, "TEAM")
    assert hasattr(mod, "LIB_AGENTS")
    assert hasattr(mod, "METADATA")
    assert mod.METADATA["name"] == "enterprise_dev"


def test_list_presets_returns_all_four():
    """list_presets 返回 4 个 preset(完成 Task 5 后)。"""
    from agentteam.presets import list_presets
    result = list_presets()
    names = sorted(p["name"] for p in result)
    assert names == ["content_marketing", "customer_support",
                     "data_analysis", "enterprise_dev"]
