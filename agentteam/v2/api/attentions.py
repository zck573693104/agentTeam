"""Attention 路由:注意件收件箱(默认人工入口)+ 决定。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agentteam.v2.api.deps import V2Components


def attentions_router(v2: V2Components) -> APIRouter:
    router = APIRouter(prefix="/attentions", tags=["v2-attentions"])

    @router.get("")
    def list_attentions(status: str = "pending", limit: int = 100):
        if status not in ("pending", "decided"):
            raise HTTPException(status_code=422, detail="status must be pending|decided")
        return v2.attention_repo.list_by_status(status, limit=limit)

    @router.post("/{attention_id}/decide")
    def decide_attention(attention_id: str, body: dict):
        """人工决定:dsh 语义——
        - approval: approve(授权延续并执行) / reject(该操作终止)
        - question: answer(注入人工回答,解除阻塞)
        """
        decision = body.get("decision", "")
        answer = body.get("answer")
        by = body.get("by", "human")
        reason = body.get("reason")
        if decision == "answer" and not (answer or "").strip():
            raise HTTPException(status_code=422, detail="answer 决策需要 answer 文本")
        try:
            return v2.scheduler.resume_attention(
                attention_id, decision=decision, decided_by=by,
                answer=answer, reason=reason,
            )
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    return router
