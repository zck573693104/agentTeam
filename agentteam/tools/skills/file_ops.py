from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool


@tool
def read_file(path: str) -> str:
    """读取指定路径文本文件的内容。"""
    return Path(path).read_text(encoding="utf-8")


@tool
def write_file(path: str, content: str) -> str:
    """将 content 写入指定路径的文件，已存在则覆盖。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} characters to {path}"


@tool
def list_dir(path: str) -> str:
    """列出目录下的条目，按名字排序，换行分隔。"""
    entries = sorted(p.name for p in Path(path).iterdir())
    return "\n".join(entries)
