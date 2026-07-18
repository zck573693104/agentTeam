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
