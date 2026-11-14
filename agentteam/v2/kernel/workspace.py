"""每 Ticket 独立工作目录(dsh: 每 Ticket 一个 Session + 独立目录)。

文件工具按 session 注入(替代 v1 的全局 WORKSPACE_ROOT env),
路径校验逻辑移植自 v1 tools/skills/file_ops.py(拒绝穿越/拒绝符号链接)。
"""
from __future__ import annotations

from pathlib import Path

from langchain_core.tools import BaseTool, tool

MAX_READ_SIZE = 10 * 1024 * 1024  # 10 MB

# v1 全局文件工具名;v2 session 构建工具列表时排除它们,改用 workspace 版
FILE_TOOL_NAMES = frozenset({"read_file", "write_file", "list_dir"})


class WorkspaceManager:
    """管理 workspaces 根目录;每个 ticket 一个子目录。"""

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def for_ticket(self, ticket_id: str) -> Path:
        d = self._root / ticket_id
        d.mkdir(parents=True, exist_ok=True)
        return d


def _validate_path(root: Path, path: str) -> Path:
    """路径必须落在 workspace 内(相对路径按 root 解析;拒绝符号链接与穿越)。"""
    p = Path(path) if Path(path).is_absolute() else root / path
    if p.is_symlink():
        raise ValueError("symlinks not allowed")
    resolved = p.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError(f"路径 '{path}' 在 workspace 外(allowed: {root})")
    return resolved


def make_file_tools(workspace_dir: Path) -> list[BaseTool]:
    """构建绑定到指定 workspace 的文件工具(read_file/write_file/list_dir)。"""
    root = Path(workspace_dir).resolve()

    @tool
    def read_file(path: str) -> str:
        """读取指定路径文本文件的内容。路径必须在 workspace 内。"""
        p = _validate_path(root, path)
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
        """将 content 写入指定路径的文件,已存在则覆盖。路径必须在 workspace 内。"""
        p = _validate_path(root, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} characters to {path}"

    @tool
    def list_dir(path: str) -> str:
        """列出目录下的条目,按名字排序,换行分隔。路径必须在 workspace 内。"""
        p = _validate_path(root, path)
        entries = sorted(item.name for item in p.iterdir())
        return "\n".join(entries) or "(空目录)"

    return [read_file, write_file, list_dir]
