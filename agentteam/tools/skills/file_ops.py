"""文件操作内置技能:read_file / write_file / list_dir。

所有路径校验:必须在 WORKSPACE_ROOT(默认当前目录)内,拒绝路径穿越。
可通过环境变量 WORKSPACE_ROOT 自定义工作区根目录。
"""
from __future__ import annotations

import os
from pathlib import Path

from langchain_core.tools import tool


MAX_READ_SIZE = 10 * 1024 * 1024  # 10 MB


def _workspace_root() -> Path:
    """获取工作区根目录(可通过 WORKSPACE_ROOT 环境变量自定义)。"""
    return Path(os.environ.get("WORKSPACE_ROOT", ".")).resolve()


def _validate_path(path: str) -> Path:
    """校验路径在 workspace 内,返回 resolved Path。越界抛 ValueError。

    - 相对路径: 相对 WORKSPACE_ROOT 解析
    - 绝对路径: 直接 resolve,然后检查是否在 workspace 内
    - 越界(如 ../secret、/etc/passwd): 抛 ValueError
    - 符号链接: 一律拒绝(保守策略,防止 LLM 工具逃逸 workspace)
    """
    root = _workspace_root()
    if Path(path).is_absolute():
        p = Path(path)
    else:
        p = root / path
    resolved = p.resolve()
    if p.is_symlink():
        raise ValueError("symlinks not allowed")
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError(
            f"路径 '{path}' 在 workspace 外(allowed: {root})"
        )
    return resolved


@tool
def read_file(path: str) -> str:
    """读取指定路径文本文件的内容。路径必须在 workspace 内。"""
    p = _validate_path(path)
    size = p.stat().st_size
    if size > MAX_READ_SIZE:
        return f"错误: 文件过大({size} bytes),最大支持 {MAX_READ_SIZE} bytes"
    with open(p, "rb") as f:
        chunk = f.read(8192)
    if b"\x00" in chunk:
        return "错误: 文件可能为二进制,无法以文本方式读取"
    return p.read_text(encoding="utf-8")


@tool
def write_file(path: str, content: str) -> str:
    """将 content 写入指定路径的文件，已存在则覆盖。路径必须在 workspace 内。"""
    p = _validate_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} characters to {path}"


@tool
def list_dir(path: str) -> str:
    """列出目录下的条目，按名字排序，换行分隔。路径必须在 workspace 内。"""
    p = _validate_path(path)
    entries = sorted(item.name for item in p.iterdir())
    return "\n".join(entries)
