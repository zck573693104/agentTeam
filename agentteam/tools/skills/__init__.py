from __future__ import annotations

from agentteam.tools.registry import ToolRegistry
from agentteam.tools.skills.file_ops import list_dir, read_file, write_file

_BUILTIN_TOOLS = [read_file, write_file, list_dir]


def register_builtin_skills(registry: ToolRegistry) -> None:
    """把内置原生技能注册到 registry。"""
    for t in _BUILTIN_TOOLS:
        registry.register(t)
