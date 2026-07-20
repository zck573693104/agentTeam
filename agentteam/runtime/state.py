from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from langgraph.graph.message import add_messages


def merge_dicts(left: dict, right: dict) -> dict:
    """合并两个 dict，right 覆盖 left 的同名键。"""
    return {**left, **right}


def set_union(left: set, right: set) -> set:
    """合并两个 set(left ∪ right)。

    作为 LangGraph Annotated reducer 使用:dag 模式下多个 worker 并行
    返回 {completed_steps: {step_id}} 时,通过此 reducer 合并。
    """
    return left | right


# DAG/sequential 执行模式类型(P3-6:原为裸 str,typo 如 "DAG" 不会被类型检查发现)
ExecutionMode = Literal["sequential", "dag"]


class Step(TypedDict, total=False):
    """计划中的一步。

    total=False:除 worker/instruction 外的字段均可选(sequential 模式不填 dag 字段)。
    原仅声明 3 字段,运行时 plan step 实际含 id/depends_on/condition(P1-2),
    导致 TypedDict 形同虚设、mypy 误报。
    """

    worker: str
    instruction: str
    status: str  # pending | running | done | failed
    # —— DAG 模式字段 ——
    id: str  # step 唯一标识,dag 路由用
    depends_on: list[str]  # 依赖的 step id 列表
    condition: str | None  # 执行条件表达式(None 表示无条件执行)


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
    total_tokens: Annotated[int, operator.add]
    # 跨层执行路径追踪，如 "team:dev.ceo.cto"
    path: str
    # —— SP6-P1: DAG 模式字段 ——
    execution_mode: ExecutionMode  # 默认 "sequential"
    completed_steps: Annotated[set[str], set_union]  # dag 模式: 已完成的 step id
    skipped_steps: Annotated[set[str], set_union]  # dag 模式: condition=False 跳过的 step id


def is_rejected(state: dict) -> bool:
    """检查状态中是否有被拒绝的审批。"""
    pending = state.get("pending_approval")
    return pending is not None and not pending.get("approved", True)


class WorkerState(TypedDict):
    """Worker 子图状态。

    共享 key（与 TeamState 同名）由 LangGraph 自动映射到父图；
    worker 内部 key 不映射回 TeamState，子图内部管理。
    """

    # —— 与 TeamState 共享 ——
    messages: Annotated[list, add_messages]
    plan: list[Step]
    current_step: int
    run_id: str
    pending_approval: dict | None
    audit_events: Annotated[list, operator.add]
    worker_outputs: Annotated[dict[str, str], merge_dicts]
    # —— Worker 内部 ——
    react_messages: Annotated[list, add_messages]
    tool_calls: list[dict]
    iteration: int
    final_answer: str
    total_tokens: Annotated[int, operator.add]
    # —— SP6-P1: DAG 模式字段(与 TeamState 共享,worker 回传 completed_steps) ——
    current_step_id: str  # dag 模式: 当前执行的 step id
    execution_mode: ExecutionMode  # 从父图透传
    completed_steps: Annotated[set[str], set_union]  # worker 返回 {step_id} 经 reducer 合并
    skipped_steps: Annotated[set[str], set_union]  # 透传
