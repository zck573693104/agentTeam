# agentteam/runtime/approval.py
"""审批门节点：worker 级和 step 级审批，使用 LangGraph interrupt() 实现。"""
from __future__ import annotations

from typing import Callable

from langgraph.types import interrupt

from agentteam.domain.approval import ApprovalPolicy
from agentteam.runtime.state import TeamState
from agentteam.runtime.trace import TraceWriter


def _should_approve(policy: ApprovalPolicy, target: str | None = None) -> bool:
    """检查审批策略是否适用于当前目标。"""
    if policy.targets is None:
        return True
    return target in policy.targets


def _make_approval_gate(
    gate_type: str,
    target_field: str,
    resolve_target: Callable[[TeamState], object | None],
    policy: ApprovalPolicy | None,
    trace_writer: TraceWriter | None = None,
    audit_repo=None,
):
    """审批门节点的共享工厂。

    封装 step / worker 两种审批门共有的 interrupt/audit/trace 逻辑。
    所有 DB 副作用放在 interrupt() 之后，避免 resume 时重复执行。

    gate_type: "step" 或 "worker"，用于 interrupt payload、trace 事件与返回值。
    target_field: interrupt payload 与 trace 事件中的目标字段名（"step"/"worker"）。
    resolve_target: 从 state 中解析目标值；返回 None 表示 no-op（不进入 interrupt）。
    """
    gate_label = gate_type.capitalize()

    def gate(state: TeamState) -> dict:
        if policy is None or policy.level != gate_type:
            return {}

        target_value = resolve_target(state)
        if target_value is None:
            return {}

        run_id = state.get("run_id", "")

        # interrupt() 在首次执行时暂停图；resume 时返回决策值
        decision = interrupt(
            {
                "gate": gate_type,
                target_field: target_value,
                "message": f"{gate_label} {target_value} 需要审批",
            }
        )

        # 以下代码仅在 resume 时执行（一次）
        approved = decision.get("approved", False)
        decider = decision.get("decider", "unknown")

        if audit_repo is not None:
            approval_id = audit_repo.add_approval(run_id)
            audit_repo.decide_approval(
                approval_id, "approved" if approved else "rejected", decider
            )

        if trace_writer is not None:
            trace_writer.emit(
                run_id,
                "approval_requested",
                "system",
                {"gate": gate_type, target_field: target_value},
            )
            trace_writer.emit(
                run_id,
                "approval_decided",
                decider,
                {"gate": gate_type, "approved": approved},
            )

        if not approved:
            return {"pending_approval": {"gate": gate_type, "approved": False}}
        return {"pending_approval": None}

    return gate


def make_step_gate(
    policy: ApprovalPolicy | None,
    trace_writer: TraceWriter | None = None,
    audit_repo=None,
):
    """创建 step 级审批门。

    每步执行前触发 interrupt()。无策略或无更多步骤时为 no-op。
    """

    def resolve_step(state: TeamState) -> int | None:
        current = state.get("current_step", 0)
        plan = state.get("plan", [])
        if current >= len(plan):
            return None
        return current

    return _make_approval_gate(
        gate_type="step",
        target_field="step",
        resolve_target=resolve_step,
        policy=policy,
        trace_writer=trace_writer,
        audit_repo=audit_repo,
    )


def make_worker_gate(
    worker_name: str,
    policy: ApprovalPolicy | None,
    trace_writer: TraceWriter | None = None,
    audit_repo=None,
):
    """创建 worker 级审批门。

    worker 执行前触发 interrupt()。无策略或 worker 不在 targets 中时为 no-op。
    """

    def resolve_worker(state: TeamState) -> str | None:
        if not _should_approve(policy, worker_name):
            return None
        return worker_name

    return _make_approval_gate(
        gate_type="worker",
        target_field="worker",
        resolve_target=resolve_worker,
        policy=policy,
        trace_writer=trace_writer,
        audit_repo=audit_repo,
    )
