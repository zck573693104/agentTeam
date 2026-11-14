"""Ticket — 已授权工作单元(dsh: 状态机独立于执行器)。

run 概念在 v2 消失:Ticket 是持久化工作单元,Session 是它的执行尝试
(一个 Ticket 可有多个 session = 重试/恢复)。
"""
from __future__ import annotations

from dataclasses import dataclass

from agentteam.v2.domain.events import TicketStatus
from agentteam.v2.domain.mandate import Mandate


@dataclass
class Ticket:
    id: str
    title: str
    task: str
    status: TicketStatus
    worker_name: str | None = None
    thread_id: str | None = None
    repo_path: str | None = None     # 关联 git 仓库(A2: workspace 升级为 worktree)
    budget_tokens: int = 0           # Token 预算(A1);0 = 不限额,超限转 Attention
    mandate: Mandate | None = None
    result: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    ended_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "task": self.task,
            "status": self.status.value,
            "worker_name": self.worker_name,
            "thread_id": self.thread_id,
            "repo_path": self.repo_path,
            "budget_tokens": self.budget_tokens,
            "mandate": self.mandate.to_dict() if self.mandate else None,
            "result": self.result,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "ended_at": self.ended_at,
        }
