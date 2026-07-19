"""插件自动发现(基于 importlib.metadata.entry_points)。

支持三个 entry_points group:
- `agentteam.tools`: 加载 BaseTool 实例,自动注册到 ToolRegistry
- `agentteam.presets`: 加载 preset 模块路径,合并到 PRESET_REGISTRY
- `agentteam.skills`: 加载 SkillLoader 兼容的 skill 名→内容 dict

第三方包只需在 pyproject.toml 声明 entry_points 即可被自动发现:
    [project.entry-points."agentteam.tools"]
    my_tool = "my_pkg.tools:get_my_tool"

    [project.entry-points."agentteam.presets"]
    my_team = "my_pkg.presets.my_team"

    [project.entry-points."agentteam.skills"]
    my_skill = "my_pkg.skills:get_my_skill_dict"

设计原则:
- 自动发现失败不应阻断启动:每个 entry 的 load() 用 try/except 包裹,失败仅记 logger.exception
- entry_points 仅在显式调用 discover_*() 时触发,避免 import 副作用
- 与现有手动注册(register_builtin_skills、PRESET_REGISTRY)共存而非替代

修复的 incidental bug:原 create_app 实例化 ToolRegistry() 后从不调用
register_builtin_skills,导致内置工具(read_file/search_web 等)在 API
服务端未注册,agent.tools 引用它们时运行时 KeyError。
本模块的 discover_tools() 同时负责注册内置工具 + entry_points 工具。
"""
from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any, Callable

from agentteam.logging_config import get_logger
from agentteam.tools.registry import ToolRegistry
from agentteam.tools.skills import register_builtin_skills

logger = get_logger("plugins")


def discover_tools(registry: ToolRegistry) -> int:
    """注册内置工具 + 从 entry_points "agentteam.tools" 加载第三方工具。

    注册顺序:内置工具先注册(占位),第三方工具后注册(若同名则跳过+警告,
    不覆盖内置工具——避免恶意/意外插件遮蔽 read_file 等核心能力)。

    返回:成功注册的第三方工具数(不含内置)。
    """
    # 1. 先注册内置工具(修复 create_app 不调 register_builtin_skills 的 bug)
    register_builtin_skills(registry)

    # 2. entry_points 发现第三方工具
    count = 0
    try:
        eps = entry_points(group="agentteam.tools")
    except TypeError:  # Python <3.10 的 entry_points 签名不同
        eps = entry_points().get("agentteam.tools", [])
    for ep in eps:
        try:
            tool = ep.load()
            # entry_points 声明的是函数/类/实例均可;若 load() 返回的是 callable
            # 而非 BaseTool 实例,调用它得到实例(支持工厂函数模式)
            if callable(tool) and not hasattr(tool, "name") and not hasattr(tool, "_run"):
                tool = tool()
            if tool.name in registry.list_names():
                logger.warning(
                    "plugin tool %s skipped: name conflict with existing tool",
                    tool.name,
                )
                continue
            registry.register(tool)
            count += 1
            logger.info("loaded plugin tool %s from %s", tool.name, ep.value)
        except Exception:
            logger.exception("failed to load plugin tool entry %s", ep.value)
    return count


def discover_presets() -> dict[str, str]:
    """从 entry_points "agentteam.presets" 加载第三方 preset 模块路径。

    返回 name → module_path dict,与 PRESET_REGISTRY 合并使用。
    第三方 preset 与内置 preset 同名时,内置优先(不覆盖)。
    """
    result: dict[str, str] = {}
    try:
        eps = entry_points(group="agentteam.presets")
    except TypeError:
        eps = entry_points().get("agentteam.presets", [])
    for ep in eps:
        try:
            # entry value 形如 "my_pkg.presets.team_a",load() 触发 import 验证可加载
            ep.load()
            result[ep.name] = ep.value
            logger.info("loaded plugin preset %s from %s", ep.name, ep.value)
        except Exception:
            logger.exception("failed to load plugin preset entry %s", ep.value)
    return result


def discover_skill_dirs() -> list[str]:
    """从 entry_points "agentteam.skills" 加载第三方 skill 内容 dict。

    每个 entry_points 应返回 dict[str, str](skill_name → skill_prompt)。
    SkillLoader 当前从文件系统加载,本函数返回的 dict 供未来扩展用
    (如注入到 SkillLoader 的 in-memory override)。

    返回:已加载的 entry_point value 列表(成功条目),供调用方进一步处理。
    本函数本身不修改 SkillLoader(避免引入新的耦合点),仅做发现。
    """
    loaded: list[str] = []
    try:
        eps = entry_points(group="agentteam.skills")
    except TypeError:
        eps = entry_points().get("agentteam.skills", [])
    for ep in eps:
        try:
            ep.load()
            loaded.append(ep.value)
            logger.info("loaded plugin skill provider from %s", ep.value)
        except Exception:
            logger.exception("failed to load plugin skill entry %s", ep.value)
    return loaded


def discover_all(registry: ToolRegistry) -> None:
    """启动时统一调用:注册工具 + 发现 preset + 发现 skill 提供者。

    供 create_app 在 ToolRegistry 实例化后调用。失败不抛异常(已内部捕获)。
    """
    discover_tools(registry)
    # 把第三方 preset 合并进 PRESET_REGISTRY(内置优先)
    from agentteam.presets import PRESET_REGISTRY
    for name, path in discover_presets().items():
        if name not in PRESET_REGISTRY:
            PRESET_REGISTRY[name] = path
    discover_skill_dirs()
