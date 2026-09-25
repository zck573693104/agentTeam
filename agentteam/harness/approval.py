"""三级审批在 opencode 底座上的桥接（SP8）。

分工（设计 docs/opencode-harness-design.md §3.2/§4）：

- step / worker 级：纯控制平面门。引擎 dispatch 前 `request_gate()`，
  需人工时抛 `ApprovalInterrupt`（引擎落盘续跑点后 return，RunManager 标 interrupted）；
  resume 时 `resolve()` 落 approvals 审计 + 发 approval_decided，返回决策。
- tool 级：opencode 会话 permission 规则集把 targets 内工具设为 ask，
  `permission.asked` / 轮询发现后由引擎调 `request_tool_permission()` 走同一
  park/resume 协议；resume 时除审计外还要向 opencode 回 `once`/`reject`。

与 LangGraph 引擎的语义对齐点：
- 审批未决策时不写 approvals 表（决策时 add_approval+decide_approval 一次写入）。
- 拒绝 → 引擎置 rejected，后续 dispatch 全部跳过，run 正常结束（completed）。
- `timeout_seconds` 语义（本引擎落地，LangGraph 引擎未实现）：设置后不等待人工，
  直接自动放行（decider="timeout"）。
"""
from __future__ import annotations

from typing import Any

from agentteam.domain.approval import ApprovalPolicy
from agentteam.harness import translator
from agentteam.runtime.trace import TraceWriter


class ApprovalInterrupt(Exception):
    """审批需要人工决策：引擎捕获后落盘续跑点并 return（run → interrupted）。

    gate 即 interrupt 的 payload，进 snapshot 的 park 字段，resume 时原样回放。
    """

    def __init__(self, gate: dict[str, Any]) -> None:
        super().__init__(f"approval required: {gate.get('gate')}")
        self.gate = gate


class ApprovalBroker:
    """三级审批桥：trace/审计 + interrupt 协议。无 I/O 到 opencode
    （tool 级的 permission 回复由引擎在 resume 时经 client 发出）。"""

    def __init__(self, trace_writer: TraceWriter | None, audit_repo=None) -> None:
        self._trace = trace_writer
        self._audit = audit_repo

    # ---------- 决策落账（resume 时调用） ----------

    def resolve(
        self,
        run_id: str,
        gate: dict[str, Any],
        approved: bool,
        decider: str = "api-user",
        reason: str | None = None,
    ) -> bool:
        """落 approvals 审计 + 发 approval_decided，返回决策值。

        gate 为 park 时的 interrupt payload（含 gate/worker/tools 等）。
        """
        if self._audit is not None:
            approval_id = self._audit.add_approval(run_id)
            self._audit.decide_approval(
                approval_id, "approved" if approved else "rejected", decider, reason
            )
        if self._trace is not None:
            self._trace.emit(
                run_id, "approval_decided", decider,
                {"gate": gate.get("gate"), "approved": approved,
                 **({"reason": reason} if reason else {})},
            )
        return approved

    # ---------- step / worker 级门（引擎 dispatch 前调用） ----------

    def request_gate(
        self,
        run_id: str,
        policy: ApprovalPolicy | None,
        gate_type: str,
        target: str | None = None,
    ) -> bool:
        """step/worker 级门。返回 True=放行（无策略/不匹配/超时自动放行）；
        需人工决策时抛 ApprovalInterrupt。拒绝由 resume 后 resolve 返回 False 表达。

        parity：LangGraph 引擎中 targets=None 表示全部匹配（_should_approve）。
        """
        if policy is None or policy.level != gate_type:
            return True
        if gate_type == "worker" and not self._matches(policy, target):
            return True
        # 超时语义：不阻塞人工，直接自动放行
        if policy.timeout_seconds is not None:
            if self._trace is not None:
                self._trace.emit(
                    run_id, "approval_decided", "timeout",
                    {"gate": gate_type, "approved": True,
                     "target": target, "auto": True},
                )
            return True

        gate: dict[str, Any] = {"gate": gate_type}
        if target is not None:
            gate[gate_type] = target
        if self._trace is not None:
            self._trace.emit(
                run_id, "approval_requested", "system", gate
            )
        raise ApprovalInterrupt(gate)

    # ---------- tool 级 permission 桥 ----------

    def request_tool_permission(
        self,
        run_id: str,
        policy: ApprovalPolicy | None,
        worker_name: str,
        permission,
    ) -> bool:
        """opencode pending permission → tool 级审批。

        返回 True=放行（引擎向 opencode 回 once）；False=策略不涉及该工具
        （不应发生：规则集已放行，防御性返回 True）。
        需人工时抛 ApprovalInterrupt（gate 含 session/permission id 供 resume 回复）。
        timeout 语义：自动放行（decider=timeout）。
        """
        gate: dict[str, Any] = {
            "gate": "tool",
            "worker": worker_name,
            "permission": permission.permission,
            "patterns": permission.patterns,
            "session_id": permission.session_id,
            "permission_id": permission.id,
            "message": f"Worker {worker_name} 请求调用工具: {permission.permission}",
        }
        if policy is not None and policy.timeout_seconds is not None:
            if self._trace is not None:
                self._trace.emit(
                    run_id, "approval_decided", "timeout",
                    {"gate": "tool", "approved": True,
                     "worker": worker_name, "auto": True},
                )
            return True
        if self._trace is not None:
            self._trace.emit(run_id, "approval_requested", "system", gate)
        raise ApprovalInterrupt(gate)

    def tool_needs_human(self, policy: ApprovalPolicy | None, permission) -> bool:
        """判断 pending permission 是否落在 targets 内（targets=None → 全部）。

        匹配集合由 translator 给出（翻译后类别 ∪ 原始工具名），
        permission 类别与 patterns 任一命中即需人工。
        """
        targets = translator.agentteam_tool_targets(policy)
        if not targets:
            return False
        if "*" in targets:
            return True
        names = {permission.permission, *permission.patterns}
        return bool(names & targets)

    @staticmethod
    def _matches(policy: ApprovalPolicy, target: str | None) -> bool:
        if policy.targets is None:
            return True
        return target in (policy.targets or [])
