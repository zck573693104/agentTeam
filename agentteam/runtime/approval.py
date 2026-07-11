# agentteam/runtime/approval.py
"""审批门节点：worker 级和 step 级审批，使用 LangGraph interrupt() 实现。"""
from __future__ import annotations

from langgraph.types import interrupt

from agentteam.domain.approval import ApprovalPolicy
from agentteam.runtime.state import TeamState
from agentteam.runtime.trace import TraceWriter


def _should_approve(policy: ApprovalPolicy, target: str | None = None) -> bool:
    """检查审批策略是否适用于当前目标。"""
    if policy.targets is None:
        return True
    return target in policy.targets


def make_step_gate(
    policy: ApprovalPolicy | None,
    trace_writer: TraceWriter | None = None,
    audit_repo=None,
):
    """创建 step 级审批门。

    每步执行前触发 interrupt()。无策略或无更多步骤时为 no-op。
    所有 DB 副作用放在 interrupt() 之后，避免 resume 时重复执行。
    """

    def step_gate(state: TeamState) -> dict:
        if policy is None or policy.level != "step":
            return {}

        current = state.get("current_step", 0)
        plan = state.get("plan", [])
        if current >= len(plan):
            return {}

        run_id = state.get("run_id", "")

        # interrupt() 在首次执行时暂停图；resume 时返回决策值
        decision = interrupt(
            {
                "gate": "step",
                "step": current,
                "message": f"Step {current} 需要审批",
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
                {"gate": "step", "step": current},
            )
            trace_writer.emit(
                run_id,
                "approval_decided",
                decider,
                {"gate": "step", "approved": approved},
            )

        if not approved:
            return {"pending_approval": {"gate": "step", "approved": False}}
        return {"pending_approval": None}

    return step_gate


def make_worker_gate(
    worker_name: str,
    policy: ApprovalPolicy | None,
    trace_writer: TraceWriter | None = None,
    audit_repo=None,
):
    """创建 worker 级审批门。

    worker 执行前触发 interrupt()。无策略或 worker 不在 targets 中时为 no-op。
    所有 DB 副作用放在 interrupt() 之后，避免 resume 时重复执行。
    """

    def worker_gate(state: TeamState) -> dict:
        if policy is None or policy.level != "worker":
            return {}
        if not _should_approve(policy, worker_name):
            return {}

        run_id = state.get("run_id", "")

        decision = interrupt(
            {
                "gate": "worker",
                "worker": worker_name,
                "message": f"Worker {worker_name} 需要审批",
            }
        )

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
                {"gate": "worker", "worker": worker_name},
            )
            trace_writer.emit(
                run_id,
                "approval_decided",
                decider,
                {"gate": "worker", "approved": approved},
            )

        if not approved:
            return {"pending_approval": {"gate": "worker", "approved": False}}
        return {"pending_approval": None}

    return worker_gate
