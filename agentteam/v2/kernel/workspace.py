"""每 Ticket 独立工作目录(dsh: 每 Ticket 一个 Session + 独立目录)。

A2: 关联 git 仓库的工单,工作区升级为 **git worktree**(分支 agentteam/<ticket>)——
文件/分支级隔离,done 后可 diff 可合并;非 git 工单退化为普通目录。

文件工具按 session 注入(替代 v1 的全局 WORKSPACE_ROOT env),
路径校验逻辑移植自 v1 tools/skills/file_ops.py(拒绝穿越/拒绝符号链接)。
"""
from __future__ import annotations

import difflib
import subprocess
from pathlib import Path

from langchain_core.tools import BaseTool, tool

MAX_READ_SIZE = 10 * 1024 * 1024  # 10 MB

# v1 全局文件工具名;v2 session 构建工具列表时排除它们,改用 workspace 版
FILE_TOOL_NAMES = frozenset({"read_file", "write_file", "list_dir"})


def _git(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """执行 git 命令(文本输出)。"""
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=check, timeout=60,
    )


class WorkspaceManager:
    """管理 workspaces 根目录;每个 ticket 一个子目录或 git worktree。"""

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def for_ticket(self, ticket_id: str, repo_path: str | None = None) -> Path:
        """返回工单工作区。repo_path 为有效 git 仓库时创建 git worktree(A2)。"""
        d = self._root / ticket_id
        if d.exists():
            return d  # 幂等(重试/恢复复用同一工作区)
        repo = Path(repo_path).resolve() if repo_path else None
        if repo and (repo / ".git").exists():
            try:
                _git(["worktree", "add", "-b", f"agentteam/{ticket_id[:12]}", str(d)],
                     cwd=repo)
                return d
            except subprocess.CalledProcessError as exc:
                # 分支已存在等场景:尝试复用;彻底失败退化为普通目录
                try:
                    _git(["worktree", "add", str(d), f"agentteam/{ticket_id[:12]}"], cwd=repo)
                    return d
                except subprocess.CalledProcessError:
                    pass  # fall through
        d.mkdir(parents=True, exist_ok=True)
        return d


def unified_diff_for_write(workspace: Path, path: str, new_content: str) -> str:
    """write_file 审批用的 unified diff(旧→新;新文件标为全量新增)。"""
    root = Path(workspace).resolve()
    target = (root / path) if not Path(path).is_absolute() else Path(path)
    try:
        resolved = target.resolve()
        inside = resolved == root or root in resolved.parents
    except OSError:
        inside = False
    if not inside:
        return "(路径在 workspace 外,无法预览)"
    old = ""
    if resolved.exists() and resolved.is_file():
        try:
            old = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return "(旧文件不可读,无法生成 diff)"
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}",
    )
    return "".join(diff) or "(无变更)"


def ticket_change_set(workspace: Path, base_ref: str | None = None) -> dict:
    """A2/B7: 工单工作区的整体变更集(git worktree 才有;普通目录返回提示)。

    返回 {is_git, base, stat, patch}。
    """
    ws = Path(workspace)
    if not (ws / ".git").exists():
        return {"is_git": False, "base": None, "stat": "", "patch": "",
                "note": "非 git 工作区,无变更集"}
    base = base_ref
    if not base:
        for cand in ("main", "master"):
            r = _git(["rev-parse", "--verify", cand], cwd=ws, check=False)
            if r.returncode == 0:
                base = cand
                break
    if not base:
        return {"is_git": True, "base": None, "stat": "", "patch": "",
                "note": "找不到基线分支(main/master)"}
    # worker 写文件不自动提交;与基线比工作树(含未提交变更)。
    # intent-to-add: 让新增文件也出现在 git diff 中(不写入内容,可撤销)
    _git(["add", "-N", "."], cwd=ws, check=False)
    stat = _git(["diff", "--stat", base], cwd=ws, check=False).stdout
    patch = _git(["diff", base], cwd=ws, check=False).stdout
    return {"is_git": True, "base": base, "stat": stat, "patch": patch[:200_000]}


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
