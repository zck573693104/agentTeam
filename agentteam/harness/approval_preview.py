"""待审批工具调用的预览构建(审批 diff 预览)。

从 park 快照的 gate(含截断后的完整入参,见 harness/approval.park_tool_call)
构造结构化预览,供 GET /api/runs/{id}/pending-approval 返回、前端渲染:

- write_file / write: 若 API 进程能读到目标文件现内容 → unified diff
  (kind="diff",Python 标准库 difflib);读不到(docker 分离部署时控制平面
  看不到 opencode 容器的 workspace,或新文件)→ 退化为新内容全文(kind="content")
- bash: 命令文本(kind="command")
- read_file / read / list_dir / list / edit: 目标路径(kind="path")
- 其余: kind="args",前端展示入参 JSON
"""
from __future__ import annotations

import difflib
import os
from pathlib import Path
from typing import Any

# 预览行数上限:diff 与 content 都截断,防止超大文件拖垮 API 响应与前端渲染
_DIFF_MAX_LINES = 200
_CONTENT_MAX_LINES = 400
# 现文件读取的大小上限(与 file_ops.MAX_READ_SIZE 对齐),超过不读
_MAX_CURRENT_FILE_BYTES = 10 * 1024 * 1024

_WRITE_TOOLS = {"write_file", "write"}
_PATH_TOOLS = {"read_file", "read", "list_dir", "list", "edit"}
# _cap_tool_input 截断字符串时追加的标记后缀
_TRUNCATION_MARKER = "...[truncated"


def build_approval_preview(gate: dict[str, Any] | None) -> dict[str, Any] | None:
    """gate → 预览 dict。gate 为空(step/worker 级门)返回 None。"""
    if not gate:
        return None
    tool = str(gate.get("permission") or "")
    input_data = gate.get("input")
    if not isinstance(input_data, dict):
        input_data = {}
    base: dict[str, Any] = {"kind": "args", "tool": tool, "truncated": False}

    if tool in _WRITE_TOOLS:
        return _write_preview(tool, input_data, base)
    if tool == "bash":
        command = str(input_data.get("command") or "").strip()
        if command:
            return {**base, "kind": "command", "command": command}
        return base
    if tool in _PATH_TOOLS:
        path = str(input_data.get("path") or "")
        if path:
            return {**base, "kind": "path", "path": path}
    return base


def _write_preview(
    tool: str, input_data: dict[str, Any], base: dict[str, Any]
) -> dict[str, Any]:
    """write 类审批的预览:优先 unified diff,读不到现文件退化为内容全文。"""
    path = str(input_data.get("path") or "")
    content = input_data.get("content")
    if not isinstance(content, str) or not content:
        return base
    # 入参在 gate 里已被截断时如实标记
    if _TRUNCATION_MARKER in content[-80:]:
        base["truncated"] = True

    current = _read_current_file(path)
    if current is not None:
        diff_lines = list(difflib.unified_diff(
            current.splitlines(),
            content.splitlines(),
            fromfile=f"a/{path or 'file'}",
            tofile=f"b/{path or 'file'}",
            n=3,
            lineterm="",
        ))
        truncated = base["truncated"]
        if len(diff_lines) > _DIFF_MAX_LINES:
            diff_lines = diff_lines[:_DIFF_MAX_LINES]
            truncated = True
        return {
            **base,
            "kind": "diff",
            "path": path,
            "diff": "\n".join(diff_lines),
            "truncated": truncated,
        }

    lines = content.splitlines()
    total_lines = len(lines)
    truncated = base["truncated"]
    if total_lines > _CONTENT_MAX_LINES:
        lines = lines[:_CONTENT_MAX_LINES]
        truncated = True
    return {
        **base,
        "kind": "content",
        "path": path,
        "content": "\n".join(lines),
        "total_lines": total_lines,
        "truncated": truncated,
    }


def _read_current_file(path: str) -> str | None:
    """读目标文件的现内容(供 diff)。读不到返回 None。

    - 相对路径按 WORKSPACE_ROOT 解析(与 file_ops._workspace_root 一致);
    - workspace 外的绝对路径拒绝读取(信息泄露面);
    - 文件不存在(新文件)→ None,diff 无意义;
    - docker 分离部署下控制平面看不到 opencode 容器的 workspace → None。
    """
    if not path:
        return None
    try:
        root = Path(os.environ.get("WORKSPACE_ROOT", ".")).resolve()
        p = Path(path)
        if not p.is_absolute():
            p = root / path
        resolved = p.resolve()
        resolved.relative_to(root)
        if not resolved.is_file():
            return None
        if resolved.stat().st_size > _MAX_CURRENT_FILE_BYTES:
            return None
        return resolved.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
