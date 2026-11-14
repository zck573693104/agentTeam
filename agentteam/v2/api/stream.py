"""Ticket 事件流 SSE(订阅先于回放 + Last-Event-ID 游标去重,移植 v1 验证过的模式)。"""
from __future__ import annotations

import asyncio
import queue

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from agentteam.v2.api.deps import V2Components


def stream_router(v2: V2Components) -> APIRouter:
    router = APIRouter(tags=["v2-stream"])
    _POLL_SECONDS = 0.5

    @router.get("/tickets/{ticket_id}/stream")
    def stream_ticket(ticket_id: str, request: Request):
        if v2.ticket_repo.get(ticket_id) is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"Ticket '{ticket_id}' not found")

        async def generator():
            # 1) 先订阅再回放,防丢事件(v1 验证过的顺序)
            q: queue.Queue = v2.bus.subscribe(ticket_id)
            try:
                # Last-Event-ID: 断线重连只补发新增
                last_id = 0
                if request.headers.get("last-event-id"):
                    try:
                        last_id = int(request.headers["last-event-id"])
                    except ValueError:
                        last_id = 0
                for e in v2.event_repo.list_after(ticket_id, after_id=last_id):
                    last_id = max(last_id, e["id"])
                    yield {"id": str(e["id"]), "event": e["type"], "data": _json(e)}
                # 2) 实时扇出(游标去重:落库事件已在回放里,跳过 id <= last_id)
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        e = q.get(timeout=_POLL_SECONDS)
                    except queue.Empty:
                        # 周期性补查 DB(防他进程写入未过本进程 bus——单进程部署下是保险)
                        for e in v2.event_repo.list_after(ticket_id, after_id=last_id):
                            last_id = max(last_id, e["id"])
                            yield {"id": str(e["id"]), "event": e["type"], "data": _json(e)}
                        continue
                    if e["id"] <= last_id:
                        continue
                    last_id = e["id"]
                    yield {"id": str(e["id"]), "event": e["type"], "data": _json(e)}
            finally:
                v2.bus.unsubscribe(ticket_id, q)

        return EventSourceResponse(generator())

    return router


def _json(e: dict) -> str:
    import json
    return json.dumps(e, ensure_ascii=False, default=str)
