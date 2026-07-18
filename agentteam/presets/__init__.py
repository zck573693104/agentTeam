"""预置企业级团队目录(数据/目录职责)。

每个 preset 模块导出 3 个模块级变量:
- TEAM: Team — 主团队定义
- LIB_AGENTS: list[Agent] — 依赖的专家库 agent(可空列表)
- METADATA: dict — name/title/description/category/tags/deps_teams/deps_library

catalog 接口:
- list_presets() -> list[dict]: 返回所有 preset 的 METADATA 列表
- get_preset(name) -> ModuleType: 按 name 获取 preset 模块

HTTP 安装职责已分离到 agentteam.presets.installer(数据/传输分离),
此处通过 re-export 保持 `from agentteam.presets import install_preset_to_api`
向后兼容。
"""
from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

# Preset 注册表:name → 模块路径。后续 task 逐步填充。
PRESET_REGISTRY: dict[str, str] = {
    "enterprise_dev": "agentteam.presets.enterprise_dev",
    "customer_support": "agentteam.presets.customer_support",
    "data_analysis": "agentteam.presets.data_analysis",
    "content_marketing": "agentteam.presets.content_marketing",
}


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


# 向后兼容:install_preset_to_api 从 installer.py re-export
# 放在文件末尾,确保 get_preset 等已定义后再触发 installer 导入(避免循环导入)。
from agentteam.presets.installer import install_preset_to_api  # noqa: E402

__all__ = ["PRESET_REGISTRY", "list_presets", "get_preset", "install_preset_to_api"]
