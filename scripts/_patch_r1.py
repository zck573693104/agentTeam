"""一次性补丁 R1 后端剩余:repos 方法/审批 diff/scheduler/路由/A4 广播。"""
import io
import ast


def patch(path, pairs):
    s = io.open(path, encoding="utf-8").read()
    for old, new in pairs:
        assert old in s, f"pattern not found in {path}: {old[:60]!r}"
        s = s.replace(old, new, 1)
    ast.parse(s)
    io.open(path, "w", encoding="utf-8", newline="\n").write(s)
    print("patched", path)


# ===== 1) tickets.py repo: add_tokens / ticket_tokens / update_budget =====
patch("agentteam/v2/storage/tickets.py", [
    ('''    def set_iterations(self, session_id: str, iterations: int) -> None:
        self._execute(
            "UPDATE sessions SET iterations = ?, updated_at = ? WHERE id = ?",
            (iterations, _now(), session_id),
        )''',
     '''    def set_iterations(self, session_id: str, iterations: int) -> None:
        self._execute(
            "UPDATE sessions SET iterations = ?, updated_at = ? WHERE id = ?",
            (iterations, _now(), session_id),
        )

    def add_tokens(self, session_id: str, tokens_in: int, tokens_out: int) -> None:
        """A1 token 记账(累加;预算门按 ticket 汇总)。"""
        self._execute(
            "UPDATE sessions SET tokens_in = tokens_in + ?, tokens_out = tokens_out + ?, "
            "updated_at = ? WHERE id = ?",
            (tokens_in, tokens_out, _now(), session_id),
        )'''),
    ('''    def cancel(self, ticket_id: str) -> bool:''',
     '''    def ticket_tokens(self, ticket_id: str) -> int:
        """A1: 工单全部会话的 token 总量(in+out)。"""
        row = self._fetchone(
            "SELECT COALESCE(SUM(tokens_in + tokens_out), 0) AS t FROM sessions "
            "WHERE ticket_id = ?",
            (ticket_id,),
        )
        return int(row["t"] or 0)

    def update_budget(self, ticket_id: str, budget_tokens: int) -> None:
        """A1: 预算加额(人工批准后)。"""
        self._execute(
            "UPDATE tickets SET budget_tokens = ?, updated_at = ? WHERE id = ?",
            (budget_tokens, _now(), ticket_id),
        )

    def cancel(self, ticket_id: str) -> bool:'''),
])

# ===== 2) attention.py: write_file 审批带 unified diff =====
patch("agentteam/v2/kernel/attention.py", [
    ('''from agentteam.v2.domain import events as ev
from agentteam.v2.kernel.pipeline import ToolContext''',
     '''from agentteam.v2.domain import events as ev
from agentteam.v2.kernel.pipeline import ToolContext
from agentteam.v2.kernel.workspace import unified_diff_for_write'''),
    ('''            payload = {
                "tool": tool.name,
                "args": {k: (str(v)[:300]) for k, v in args.items()},
                "worker": ctx.worker_name,
                "tool_call_id": ctx.tool_call_id,
                "decision_effect": "approve=授权延续并执行;reject=该操作终止(worker 可改道)",
            }''',
     '''            payload = {
                "tool": tool.name,
                "args": {k: (str(v)[:300]) for k, v in args.items()},
                "worker": ctx.worker_name,
                "tool_call_id": ctx.tool_call_id,
                "decision_effect": "approve=授权延续并执行;reject=该操作终止(worker 可改道)",
            }
            # B7 diff review: 写文件审批展示 unified diff(旧→新)
            if tool.name == "write_file":
                try:
                    payload["diff"] = unified_diff_for_write(
                        Path(ctx.workspace_dir), args.get("path", ""),
                        str(args.get("content", "")),
                    )[:8000]
                except Exception:
                    pass'''),
    ('''from typing import Callable

from langchain_core.tools import BaseTool''',
     '''from pathlib import Path
from typing import Callable

from langchain_core.tools import BaseTool'''),
])

# ===== 3) scheduler.py: repo/budget 参数 + 预算加额 + fan_out_thread =====
patch("agentteam/v2/kernel/scheduler.py", [
    ('''    def submit_request(
        self, text: str, submitted_by: str = "api",
        worker_name: str | None = None, thread_id: str | None = None,
        mandate_basis: str = "explicit",
    ) -> dict:''',
     '''    def submit_request(
        self, text: str, submitted_by: str = "api",
        worker_name: str | None = None, thread_id: str | None = None,
        mandate_basis: str = "explicit",
        repo_path: str | None = None, budget_tokens: int = 0,
    ) -> dict:'''),
    ('''        ticket = Ticket(
            id=uuid4().hex,
            title=(decision.note or text)[:60],
            task=decision.goal,
            status=TicketStatus.PENDING,
            worker_name=decision.worker_name,
            thread_id=thread_id,
            mandate=Mandate(''',
     '''        ticket = Ticket(
            id=uuid4().hex,
            title=(decision.note or text)[:60],
            task=decision.goal,
            status=TicketStatus.PENDING,
            worker_name=decision.worker_name,
            thread_id=thread_id,
            repo_path=repo_path,
            budget_tokens=max(0, int(budget_tokens or 0)),
            mandate=Mandate('''),
    ('''        if attention["type"] == "budget" and decision == "approve":''',
     '''        if attention["type"] == "budget" and decision == "approve":''' + "\n            " + "pass"),
])
# resume_attention: budget approve → 预算翻倍落库(在 cas waiting→running 之前)
s = io.open("agentteam/v2/kernel/scheduler.py", encoding="utf-8").read()
old = '''        # waiting_human → running;线程池恢复会话
        self.sessions.cas_status(att["session_id"], SessionStatus.WAITING_HUMAN, SessionStatus.RUNNING)'''
new = '''        # A1: budget+approve → 预算翻倍落库(授权延续)
        if att["type"] == "budget" and decision == "approve":
            ticket_obj = self.tickets.get(att["ticket_id"])
            if ticket_obj and ticket_obj.budget_tokens:
                self.tickets.update_budget(att["ticket_id"], ticket_obj.budget_tokens * 2)
        # waiting_human → running;线程池恢复会话
        self.sessions.cas_status(att["session_id"], SessionStatus.WAITING_HUMAN, SessionStatus.RUNNING)'''
assert old in s
s = s.replace(old, new)

# A4: fan_out_thread(同 thread 活跃工单广播,dsh 双向 pass-through)
old2 = '''    def cancel(self, ticket_id: str, by: str = "human") -> bool:'''
new2 = '''    def fan_out_thread(self, thread_id: str, text: str, by: str = "source") -> int:
        """A4 双向 pass-through(dsh): 同线程事件广播给全部活跃订阅工单。

        worker 认领后同线程新事件直达 worker(不过 Router),多 worker
        同线程时各自可见、冲突自查。终态工单跳过(不复活)。
        """
        delivered = 0
        for ticket_id in self.subscriptions.tickets_for_thread(thread_id):
            t = self.tickets.get(ticket_id)
            if t is None or t.status.is_terminal():
                continue
            with self._lock:
                self._follow_ups.setdefault(ticket_id, []).append((text, by))
            self._emit(ticket_id, ev.FOLLOW_UP_DELIVERED, actor=by, payload={
                "text": text[:500], "state": t.status.value, "via": "thread_fanout",
            })
            self._wake.set()
            delivered += 1
        return delivered

    def cancel(self, ticket_id: str, by: str = "human") -> bool:'''
assert old2 in s
s = s.replace(old2, new2)
ast.parse(s)
io.open("agentteam/v2/kernel/scheduler.py", "w", encoding="utf-8", newline="\n").write(s)
print("patched agentteam/v2/kernel/scheduler.py")

# ===== 4) inbound.py: trigger 记录后向活跃工单广播(A4) =====
patch("agentteam/v2/kernel/inbound.py", [
    ('''        thread = self.threads.get_or_create(source_name, trigger.thread_key)
        trig = self.threads.append_trigger(
            thread["id"], trigger.actor, trigger.text, raw=trigger.raw,
        )''',
     '''        thread = self.threads.get_or_create(source_name, trigger.thread_key)
        trig = self.threads.append_trigger(
            thread["id"], trigger.actor, trigger.text, raw=trigger.raw,
        )
        # A4 双向 pass-through: 同线程活跃工单的 worker 直接收到此事件
        # (不改变本事件的 gate/Eval 流程;广播与立项互相独立)
        self._fanout = getattr(self, "_fanout", None)
        if self._fanout is not None:
            self._fanout(thread["id"], trigger.text, trigger.actor)'''),
])

# ===== 5) 路由: requests 透传 repo/budget;diff 端点 =====
patch("agentteam/v2/api/tickets.py", [
    ('''            return v2.scheduler.submit_request(
                text=body.get("text", ""),
                submitted_by=body.get("submitted_by", "api"),
                worker_name=body.get("worker_name"),
                thread_id=body.get("thread_id"),
            )''',
     '''            return v2.scheduler.submit_request(
                text=body.get("text", ""),
                submitted_by=body.get("submitted_by", "api"),
                worker_name=body.get("worker_name"),
                thread_id=body.get("thread_id"),
                repo_path=body.get("repo_path"),
                budget_tokens=int(body.get("budget_tokens") or 0),
            )'''),
    ('''    @router.get("/tickets/{ticket_id}/notifications")''',
     '''    @router.get("/tickets/{ticket_id}/diff")
    def ticket_diff(ticket_id: str):
        """A2/B7: 工单变更集(git worktree 工单: 与基线分支的 stat+patch)。"""
        t = v2.ticket_repo.get(ticket_id)
        if t is None:
            raise HTTPException(status_code=404, detail=f"Ticket '{ticket_id}' not found")
        from agentteam.v2.kernel.workspace import ticket_change_set
        sessions = v2.session_repo.list_for_ticket(ticket_id)
        if not sessions:
            raise HTTPException(status_code=409, detail="工单尚无会话")
        workspace = sessions[-1]["workspace_dir"]
        return ticket_change_set(Path(workspace), base_ref=None)

    @router.get("/tickets/{ticket_id}/notifications")'''),
    ('''"""Ticket 路由:请求入口 / 看板 / 详情 / transcript / 取消。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException''',
     '''"""Ticket 路由:请求入口 / 看板 / 详情 / transcript / 变更集 / 取消。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException'''),
])

# ===== 6) deps.py: InboundEngine 注入 fanout =====
patch("agentteam/v2/api/deps.py", [
    ('''    inbound_engine = InboundEngine(
        source_repo=source_repo, thread_repo=thread_repo,
        evaluation_repo=evaluation_repo, eval_engine=eval_engine,
        event_repo=event_repo, bus=bus,
        submit_from_source=scheduler.submit_from_source,
    )''',
     '''    inbound_engine = InboundEngine(
        source_repo=source_repo, thread_repo=thread_repo,
        evaluation_repo=evaluation_repo, eval_engine=eval_engine,
        event_repo=event_repo, bus=bus,
        submit_from_source=scheduler.submit_from_source,
        fanout=scheduler.fan_out_thread,
    )'''),
])

patch("agentteam/v2/kernel/inbound.py", [
    ('''        event_repo: EventRepo,
        bus: EventBus,
        submit_from_source,  # Scheduler.submit_request 的 inferred 封装
    ) -> None:''',
     '''        event_repo: EventRepo,
        bus: EventBus,
        submit_from_source,  # Scheduler.submit_request 的 inferred 封装
        fanout=None,         # Scheduler.fan_out_thread(A4 双向 pass-through)
    ) -> None:'''),
    ('''        self._submit = submit_from_source''',
     '''        self._submit = submit_from_source
        self._fanout = fanout'''),
])

print("ALL PATCHED")
