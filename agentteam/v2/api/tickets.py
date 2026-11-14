"""Ticket 路由:请求入口 / 看板 / 详情 / transcript / 取消。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agentteam.v2.api.deps import V2Components
from agentteam.v2.domain.events import TicketStatus


def tickets_router(v2: V2Components) -> APIRouter:
    router = APIRouter(tags=["v2-tickets"])

    @router.post("/requests")
    def submit_request(body: dict):
        """人/Source 的统一入口:Router 决策 → reply 或 Ticket。

        dsh: 不是所有消息都要立项;Router 可直接答复。
        """
        try:
            return v2.scheduler.submit_request(
                text=body.get("text", ""),
                submitted_by=body.get("submitted_by", "api"),
                worker_name=body.get("worker_name"),
                thread_id=body.get("thread_id"),
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @router.get("/tickets")
    def list_tickets(status: str | None = None, limit: int = 200):
        st = None
        if status:
            try:
                st = TicketStatus(status)
            except ValueError:
                raise HTTPException(status_code=422, detail=f"invalid status: {status}")
        return [t.to_dict() for t in v2.ticket_repo.list_by_status(st, limit=min(limit, 500))]

    @router.get("/tickets/{ticket_id}")
    def get_ticket(ticket_id: str):
        t = v2.ticket_repo.get(ticket_id)
        if t is None:
            raise HTTPException(status_code=404, detail=f"Ticket '{ticket_id}' not found")
        d = t.to_dict()
        d["sessions"] = v2.session_repo.list_for_ticket(ticket_id)
        return d

    @router.get("/tickets/{ticket_id}/messages")
    def ticket_messages(ticket_id: str, session_id: str | None = None):
        """transcript;默认返回最近一个 session 的完整轨迹。"""
        if v2.ticket_repo.get(ticket_id) is None:
            raise HTTPException(status_code=404, detail=f"Ticket '{ticket_id}' not found")
        sessions = v2.session_repo.list_for_ticket(ticket_id)
        if not sessions:
            return []
        target = session_id or sessions[-1]["id"]
        return v2.message_repo.list_for_session(target)

    @router.post("/tickets/{ticket_id}/cancel")
    def cancel_ticket(ticket_id: str, body: dict | None = None):
        """撤权(dsh: 人的停止 = revoked_by_human);pending/running/waiting 均可。"""
        t = v2.ticket_repo.get(ticket_id)
        if t is None:
            raise HTTPException(status_code=404, detail=f"Ticket '{ticket_id}' not found")
        by = (body or {}).get("by", "human")
        if v2.scheduler.cancel(ticket_id, by=by):
            return {"ok": True, "revoked_by_human": True}
        raise HTTPException(status_code=409, detail="ticket 已终态,不可撤权")

    @router.get("/tickets/{ticket_id}/events")
    def ticket_events(ticket_id: str, after_id: int = 0, limit: int = 500):
        """事件流 REST 读取(SSE 之外的拉取通道)。"""
        if v2.ticket_repo.get(ticket_id) is None:
            raise HTTPException(status_code=404, detail=f"Ticket '{ticket_id}' not found")
        return v2.event_repo.list_after(ticket_id, after_id=after_id, limit=min(limit, 1000))

    @router.post("/tickets/{ticket_id}/messages")
    def follow_up(ticket_id: str, body: dict):
        """追问直通(dsh Subscription fan-out): 显式指向工单,直达 worker,只入不回。"""
        text = body.get("text", "")
        if not text.strip():
            raise HTTPException(status_code=422, detail="text 必填")
        try:
            return v2.scheduler.deliver_follow_up(
                ticket_id, text, by=body.get("by", "human"),
            )
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e))

    @router.get("/tickets/{ticket_id}/notifications")
    def ticket_notifications(ticket_id: str):
        """IM 通知回执(outbox: status/attempts/last_error,SP12b 语义)。"""
        if v2.ticket_repo.get(ticket_id) is None:
            raise HTTPException(status_code=404, detail=f"Ticket '{ticket_id}' not found")
        return v2.outbox_repo.list_for_ticket(ticket_id)

    return router
