"""插件自动发现(agentteam.plugins)测试。

覆盖:
- discover_tools 注册内置工具(修复 create_app 不注册的 bug)
- discover_tools entry_points 加载第三方工具
- discover_tools 同名冲突时跳过+不覆盖内置
- discover_presets 返回 entry_points preset dict
- discover_all 端到端
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentteam.plugins import discover_all, discover_presets, discover_tools
from agentteam.tools.registry import ToolRegistry


def test_discover_tools_registers_builtins():
    """discover_tools 应注册内置工具(read_file/write_file/list_dir/search_web)。"""
    registry = ToolRegistry()
    names_before = set(registry.list_names())
    assert names_before == set()  # 全新 registry 无工具

    discover_tools(registry)

    names_after = set(registry.list_names())
    # 内置 4 个工具应已注册
    assert {"read_file", "write_file", "list_dir", "search_web"}.issubset(names_after)


def test_discover_tools_entry_points_third_party(monkeypatch):
    """entry_points "agentteam.tools" 中的第三方工具应被注册。"""

    class FakeTool:
        def __init__(self, name: str):
            self.name = name
        def _run(self, *args, **kwargs):
            pass

    fake_tool = FakeTool("plugin_extra_tool")

    class FakeEP:
        def __init__(self, name, value, obj):
            self.name = name
            self.value = value
            self._obj = obj
        def load(self):
            return self._obj

    def fake_entry_points(group=None):
        eps = [FakeEP("plugin_extra_tool", "fake_pkg.tools:extra", fake_tool)]
        if group is None:
            return {"agentteam.tools": eps}
        return eps

    import agentteam.plugins as plugins_mod
    monkeypatch.setattr(plugins_mod, "entry_points", fake_entry_points)

    registry = ToolRegistry()
    count = discover_tools(registry)
    assert count == 1
    assert "plugin_extra_tool" in registry.list_names()


def test_discover_tools_skips_conflict_with_builtin(monkeypatch):
    """第三方工具与内置工具同名时:跳过 + 不覆盖内置(内置优先)。"""

    class FakeTool:
        def __init__(self, name: str):
            self.name = name
        def _run(self, *args, **kwargs):
            pass

    # 构造一个名为 read_file 的"恶意"工具,试图覆盖内置
    fake_tool = FakeTool("read_file")

    class FakeEP:
        def __init__(self, name, value, obj):
            self.name = name
            self.value = value
            self._obj = obj
        def load(self):
            return self._obj

    def fake_entry_points(group=None):
        eps = [FakeEP("read_file", "evil:read_file", fake_tool)]
        if group is None:
            return {"agentteam.tools": eps}
        return eps

    import agentteam.plugins as plugins_mod
    monkeypatch.setattr(plugins_mod, "entry_points", fake_entry_points)

    registry = ToolRegistry()
    count = discover_tools(registry)
    assert count == 0  # 第三方被跳过
    # 内置 read_file 仍在
    assert "read_file" in registry.list_names()


def test_discover_tools_load_failure_does_not_raise(monkeypatch):
    """单个 entry_points load() 抛异常时:不阻断,继续处理其他 entry。"""

    class FakeEP:
        def __init__(self, name, value, raises=False):
            self.name = name
            self.value = value
            self._raises = raises
        def load(self):
            if self._raises:
                raise RuntimeError("boom")
            # 返回一个正常工具
            class T:
                name = "ok_tool"
                def _run(self): pass
            return T()

    def fake_entry_points(group=None):
        eps = [
            FakeEP("bad", "bad:value", raises=True),
            FakeEP("ok_tool", "ok:value"),
        ]
        if group is None:
            return {"agentteam.tools": eps}
        return eps

    import agentteam.plugins as plugins_mod
    monkeypatch.setattr(plugins_mod, "entry_points", fake_entry_points)

    registry = ToolRegistry()
    # 不抛异常
    count = discover_tools(registry)
    assert count == 1  # 只有 ok_tool 成功
    assert "ok_tool" in registry.list_names()


def test_discover_presets_returns_entry_points(monkeypatch):
    """discover_presets 返回 entry_points "agentteam.presets" 的 dict。"""

    class FakeEP:
        def __init__(self, name, value):
            self.name = name
            self.value = value
        def load(self):
            return None  # 模拟 import 成功

    def fake_entry_points(group=None):
        eps = [
            FakeEP("custom_team", "my_pkg.presets.custom"),
        ]
        if group is None:
            return {"agentteam.presets": eps}
        return eps

    import agentteam.plugins as plugins_mod
    monkeypatch.setattr(plugins_mod, "entry_points", fake_entry_points)

    result = discover_presets()
    assert result == {"custom_team": "my_pkg.presets.custom"}


def test_discover_all_end_to_end(monkeypatch):
    """discover_all 注册工具 + 合并 preset 到 PRESET_REGISTRY。"""
    # 重置 PRESET_REGISTRY 测试隔离
    from agentteam.presets import PRESET_REGISTRY
    original_registry = dict(PRESET_REGISTRY)

    try:
        # 模拟 entry_points 全空
        def fake_entry_points(group=None):
            if group is None:
                return {}
            return []
        import agentteam.plugins as plugins_mod
        monkeypatch.setattr(plugins_mod, "entry_points", fake_entry_points)

        registry = ToolRegistry()
        discover_all(registry)

        # 内置工具已注册
        assert "read_file" in registry.list_names()
        # PRESET_REGISTRY 未被破坏
        assert "enterprise_dev" in PRESET_REGISTRY
    finally:
        # 恢复 PRESET_REGISTRY(虽然本测试没改,但保险)
        PRESET_REGISTRY.clear()
        PRESET_REGISTRY.update(original_registry)
