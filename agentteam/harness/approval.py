"""三级审批在 opencode v2 底座上的桥接（SP8 / v2 迁移）。

分工（设计 docs/opencode-harness-design.md §3.2/§8）：

- step / worker 级：纯控制平面门，与底座无关。引擎 dispatch 前 `request_gate()`，
  需人工时抛 `ApprovalInterrupt`（引擎落盘续跑点后 return，RunManager 标
  interrupted）；resume 时 `resolve()` 落 approvals 审计 + 发 approval_decided。
- tool 级：**v2 没有会话级 permission 规则集，也不产生 pending permission
  （实测）**，故从「事前拦住工具」改为「事后中断 + 审计」：
  引擎观测 `session.next.tool.*` 得到工具名与完整入参，命中
  ①worker 白名单外 或 ②tool 级审批 targets 时，POST interrupt 终止在飞回合、
  发 tool_denied 审计，并按策略 park 等人工；批准后引擎向同一会话补发
  「已批准，请继续」的 prompt，拒绝则整步 rejected。
  已知代价：**第一个被观测到的调用可能已经执行完毕**，中断只保证后续不再继续。

`AGENTTEAM_OC_TOOL_GUARD` 控制白名单外调用的处置（仅影响①，审批目标②恒 park）：
- "strict"（默认）：中断 + park 等人工
- "audit"：发 tool_denied 审计但继续执行
- "off"：完全不检查

与 LangGraph 引擎的语义对齐点：
- 审批未决策时不写 approvals 表（决策时 add_approval+decide_approval 一次写入）。
- 拒绝 → 引擎置 rejected，后续 dispatch 全部跳过，run 正常结束（completed）。
- `timeout_seconds` 语义：设置后不等待人工，直接自动放行（decider="timeout"）。
"""
from __future__ import annotations

import os
from typing import Any

from agentteam.domain.approval import ApprovalPolicy
from agentteam.harness import translator
from agentteam.runtime.trace import TraceWriter

TOOL_GUARD_STRICT = "strict"
TOOL_GUARD_AUDIT = "audit"
TOOL_GUARD_OFF = "off"

# 审批违规的两种成因（进审计与 gate，便于前端区分展示）
VIOLATION_NOT_WHITELISTED = "not_whitelisted"
VIOLATION_REQUIRES_APPROVAL = "requires_approval"


def tool_guard_mode() -> str:
    mode = (os.environ.get("AGENTTEAM_OC_TOOL_GUARD") or TOOL_GUARD_STRICT).strip()
    return mode if mode in (TOOL_GUARD_STRICT, TOOL_GUARD_AUDIT, TOOL_GUARD_OFF) \
        else TOOL_GUARD_STRICT


class ApprovalInterrupt(Exception):
    """审批需要人工决策：引擎捕获后落盘续跑点并 return（run → interrupted）。

    gate 即 interrupt 的 payload（进 snapshot 的 park 字段，resume 时原样回放）。
    """

    def __init__(self, gate: dict[str, Any]) -> None:
        super().__init__(f"approval required: {gate.get('gate')}")
        self.gate = gate


class ApprovalBroker:
    """三级审批桥：trace/审计 + interrupt 协议。

    不做任何 opencode I/O —— 工具中断与会话补发由引擎经 client 执行。
    """

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
        """落 approvals 审计 + 发 approval_decided，返回决策值。"""
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
            self._auto_allow(run_id, {"gate": gate_type, "target": target})
            return True

        gate: dict[str, Any] = {"gate": gate_type}
        if target is not None:
            gate[gate_type] = target
        if self._trace is not None:
            self._trace.emit(run_id, "approval_requested", "system", gate)
        raise ApprovalInterrupt(gate)

    # ---------- tool 级：事后中断判定 ----------

    @staticmethod
    def inspect_tool_call(
        policy: ApprovalPolicy | None,
        tool_name: str,
        whitelist: set[str],
    ) -> str | None:
        """返回违规成因，None 表示该调用无需处置。

        白名单外的副作用工具优先判为 not_whitelisted（AgentTeam 的 tools
        字段是硬白名单）；命中 tool 级审批 targets 判为 requires_approval。
        todowrite/todoread 属计划簿记，不参与白名单执法。
        """
        if not tool_name or tool_name in translator.NON_TOOL_NAMES:
            return None
        targets = translator.agentteam_tool_targets(policy)
        if translator.tool_in_targets(tool_name, targets):
            return VIOLATION_REQUIRES_APPROVAL
        if translator.tool_in_targets(tool_name, whitelist):
            return None
        return VIOLATION_NOT_WHITELISTED

    def park_tool_call(
        self,
        run_id: str,
        worker_name: str,
        call,
        session_id: str,
        violation: str,
    ) -> None:
        """发 tool_denied 审计 + approval_requested 并抛 ApprovalInterrupt。

        call 为 events.ObservedCall（含 name/input/call_id）。
        """
        if self._trace is not None:
            self._trace.emit(
                run_id, "tool_denied", worker_name,
                {"tools": [call.name], "call_id": call.call_id,
                 "args": call.args_preview(), "violation": violation,
                 "session_id": session_id},
            )
        gate: dict[str, Any] = {
            "gate": "tool",
            "worker": worker_name,
            "permission": call.name,
            "patterns": [call.name],
            "args": call.args_preview(),
            "call_id": call.call_id,
            "violation": violation,
            "session_id": session_id,
            "message": f"Worker {worker_name} 调用了 {call.name}"
                       f"（{violation}），已中断该回合，是否放行？",
        }
        if self._trace is not None:
            self._trace.emit(run_id, "approval_requested", "system", gate)
        raise ApprovalInterrupt(gate)

    def auto_allow_tool_call(
        self, run_id: str, worker_name: str, call, violation: str, session_id: str
    ) -> None:
        """audit 模式：只记事实（tool_denied + action=continued），不打断执行。"""
        if self._trace is not None:
            self._trace.emit(
                run_id, "tool_denied", worker_name,
                {"tools": [call.name], "call_id": call.call_id,
                 "args": call.args_preview(), "violation": violation,
                 "action": "continued", "session_id": session_id},
            )

    def review_tool_call(
        self,
        run_id: str,
        policy: ApprovalPolicy | None,
        worker_name: str,
        call,
        session_id: str,
        whitelist: set[str],
    ) -> None:
        """引擎每个轮询周期对「新观测到的工具调用」调用一次。

        静默放行 / audit 记账放行 → 正常返回；需要人工 → 抛 ApprovalInterrupt
        （引擎据此 POST interrupt 终止回合并 park）。
        """
        violation = self.inspect_tool_call(policy, call.name, whitelist)
        if violation is None:
            return
        if violation == VIOLATION_REQUIRES_APPROVAL:
            if policy is not None and policy.timeout_seconds is not None:
                self._auto_allow(run_id, {"gate": "tool", "worker": worker_name,
                                          "permission": call.name})
                return
        else:  # not_whitelisted：策略管不到，由工具白名单执法模式决定
            mode = tool_guard_mode()
            if mode == TOOL_GUARD_OFF:
                return
            if mode == TOOL_GUARD_AUDIT:
                self.auto_allow_tool_call(
                    run_id, worker_name, call, violation, session_id)
                return
        self.park_tool_call(
            run_id, worker_name, call, session_id, violation
        )

    # ---------- 内部 ----------

    @staticmethod
    def _matches(policy: ApprovalPolicy, target: str | None) -> bool:
        if policy.targets is None:
            return True
        return target in (policy.targets or [])

    def _auto_allow(self, run_id: str, gate: dict[str, Any]) -> None:
        if self._trace is not None:
            self._trace.emit(
                run_id, "approval_decided", "timeout",
                {**gate, "approved": True, "auto": True},
            )
