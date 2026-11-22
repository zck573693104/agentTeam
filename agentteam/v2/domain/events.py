"""事件词表 + Ticket/Session 状态机。

v2 事件为全集渲染设计:前端不做事件类型白名单,未知事件按 payload 透传展示,
消除 v1「新增事件必改前端白名单」的痛点(修复 commit 551fc27 一类问题)。
"""
from __future__ import annotations

from enum import Enum

# —— 事件词表(dsh 对齐:Trigger/Ticket/Session/Attention 全生命周期可观测) ——
# 生命周期: ticket_created / ticket_dispatched / ticket_status_changed / ticket_cancelled
# 会话:     session_started / session_iteration / session_ended
# 工具:     tool_called / tool_result
# 注意件:   attention_created / attention_decided(P1)
# 直通:     follow_up_delivered(P2)
# 入站:     trigger_received / gate_passed / gate_rejected / eval_decided(P3)
# 学习:     postmortem_completed / postmortem_failed / skill_proposed / skill_promoted(P4)
TICKET_CREATED = "ticket_created"
TICKET_DISPATCHED = "ticket_dispatched"
TICKET_STATUS_CHANGED = "ticket_status_changed"
TICKET_CANCELLED = "ticket_cancelled"
SESSION_STARTED = "session_started"
SESSION_ITERATION = "session_iteration"
SESSION_ENDED = "session_ended"
TOOL_CALLED = "tool_called"
TOOL_RESULT = "tool_result"
ATTENTION_CREATED = "attention_created"
ATTENTION_DECIDED = "attention_decided"
POLICY_EXPIRED = "policy_expired"
FOLLOW_UP_DELIVERED = "follow_up_delivered"
TRIGGER_RECEIVED = "trigger_received"
GATE_PASSED = "gate_passed"
GATE_REJECTED = "gate_rejected"
EVAL_DECIDED = "eval_decided"
POSTMORTEM_COMPLETED = "postmortem_completed"
POSTMORTEM_FAILED = "postmortem_failed"
SKILL_PROPOSED = "skill_proposed"
SKILL_PROMOTED = "skill_promoted"

EVENTS: tuple[str, ...] = (
    TICKET_CREATED, TICKET_DISPATCHED, TICKET_STATUS_CHANGED, TICKET_CANCELLED,
    SESSION_STARTED, SESSION_ITERATION, SESSION_ENDED,
    TOOL_CALLED, TOOL_RESULT,
    ATTENTION_CREATED, ATTENTION_DECIDED, POLICY_EXPIRED,
    FOLLOW_UP_DELIVERED,
    TRIGGER_RECEIVED, GATE_PASSED, GATE_REJECTED, EVAL_DECIDED,
    POSTMORTEM_COMPLETED, POSTMORTEM_FAILED, SKILL_PROPOSED, SKILL_PROMOTED,
)


class TicketStatus(str, Enum):
    """Ticket 状态机(dsh:工单状态独立于执行器)。

    pending → running ⇄ waiting_human → done | failed
    任意态可 cancelled(人工撤权 revoked_by_human)。
    """

    PENDING = "pending"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @classmethod
    def terminal(cls) -> frozenset["TicketStatus"]:
        return frozenset({cls.DONE, cls.FAILED, cls.CANCELLED})

    def is_terminal(self) -> bool:
        return self in self.terminal()


class SessionStatus(str, Enum):
    """Worker Session 状态(一个 Ticket 可有多个会话 = 多次执行尝试)。"""

    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
