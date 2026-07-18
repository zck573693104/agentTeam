"""预置企业级团队目录与安装 helper。

每个 preset 模块导出 3 个模块级变量:
- TEAM: Team — 主团队定义
- LIB_AGENTS: list[Agent] — 依赖的专家库 agent(可空列表)
- METADATA: dict — name/title/description/category/tags/deps_teams/deps_library

catalog 接口:
- list_presets() -> list[dict]: 返回所有 preset 的 METADATA 列表
- get_preset(name) -> ModuleType: 按 name 获取 preset 模块
- install_preset_to_api(name, api): 安装到 API 服务(见 Task 6)
"""
from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

# Preset 注册表:name → 模块路径。后续 task 逐步填充。
PRESET_REGISTRY: dict[str, str] = {}


def list_presets() -> list[dict[str, Any]]:
    """返回所有预置团队的 METADATA 列表(按 name 排序)。"""
    result = []
    for name, module_path in sorted(PRESET_REGISTRY.items()):
        mod = importlib.import_module(module_path)
        result.append(mod.METADATA)
    return result


def get_preset(name: str) -> ModuleType:
    """按 name 获取 preset 模块。不存在抛 KeyError。"""
    if name not in PRESET_REGISTRY:
        raise KeyError(
            f"Preset '{name}' not found. Available: {sorted(PRESET_REGISTRY)}"
        )
    return importlib.import_module(PRESET_REGISTRY[name])
