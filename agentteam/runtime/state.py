from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


def merge_dicts(left: dict, right: dict) -> dict:
    """合并两个 dict，right 覆盖 left 的同名键。"""
    return {**left, **right}


class Step(TypedDict):
    """计划中的一步。"""

    worker: str
    instruction: str
    status: str  # pending | running | done | failed


class TeamState(TypedDict):
    """Team 执行图的全局状态。"""

    messages: Annotated[list, add_messages]
    task: str
    plan: list[Step]
    current_step: int
    worker_outputs: Annotated[dict[str, str], merge_dicts]
    audit_events: Annotated[list, operator.add]
    run_id: str
    pending_approval: dict | None


def is_rejected(state: dict) -> bool:
    """检查状态中是否有被拒绝的审批。"""
    pending = state.get("pending_approval")
    return pending is not None and not pending.get("approved", True)
