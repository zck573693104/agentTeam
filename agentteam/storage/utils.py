"""storage 层共享工具。"""
from __future__ import annotations

from datetime import datetime, timezone


def utcnow_iso() -> str:
    """返回 UTC ISO 8601 时间戳字符串。"""
    return datetime.now(timezone.utc).isoformat()
