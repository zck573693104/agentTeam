from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ApprovalPolicy:
    """声明式审批策略，三种粒度。M2 仅定义数据结构，M3 接入 interrupt。"""

    level: Literal["worker", "tool", "step"]
    targets: list[str] | None = None
    timeout_seconds: int | None = None
