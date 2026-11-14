"""Thread/Trigger 路由:入站事件聚合(dsh: Thread = Trigger 时间序,上下文单元)。

P2 只做入站记录与聚合;gate/Eval/自动立项在 P3(Source 协议)接入。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agentteam.v2.api.deps import V2Components


def threads_router(v2: V2Components) -> APIRouter:
    router = APIRouter(prefix="/threads", tags=["v2-threads"])

    @router.post("/inbound")
    def inbound(body: dict):
        """入站事件记录:source + thread_key 幂等聚合线程,Trigger 不可变落库。

        body: {source, thread_key, actor, text, raw?}
        """
        source = (body.get("source") or "").strip()
        thread_key = (body.get("thread_key") or "").strip()
        actor = (body.get("actor") or "unknown").strip()
        text = (body.get("text") or "").strip()
        if not source or not thread_key or not text:
            raise HTTPException(status_code=422, detail="source/thread_key/text 必填")
        thread = v2.thread_repo.get_or_create(source, thread_key, title=thread_key)
        trigger = v2.thread_repo.append_trigger(
            thread["id"], actor, text, raw=body.get("raw"),
        )
        return {"thread_id": thread["id"], "trigger_id": trigger["id"]}

    @router.get("")
    def list_threads(limit: int = 100):
        return v2.thread_repo.list_threads(limit=limit)

    @router.get("/{thread_id}")
    def thread_detail(thread_id: str):
        thread = v2.thread_repo.get(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail=f"Thread '{thread_id}' not found")
        thread["triggers"] = v2.thread_repo.list_triggers(thread_id)
        ticket_ids = v2.subscription_repo.tickets_for_thread(thread_id)
        thread["tickets"] = [
            t.to_dict() for t in (v2.ticket_repo.get(tid) for tid in ticket_ids)
            if t is not None
        ]
        return thread

    return router
