"""Source 路由:入站源 CRUD / webhook 接入 / 观察模式面板(dsh SP12e)。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Header

from agentteam.v2.api.deps import V2Components
from agentteam.v2.sources.webhook import WebhookSource, hash_secret, verify_secret


def sources_router(v2: V2Components) -> APIRouter:
    router = APIRouter(prefix="/sources", tags=["v2-sources"])

    @router.post("")
    def create_source(body: dict):
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="name 必填")
        if v2.source_repo.get(name) is not None:
            raise HTTPException(status_code=400, detail=f"Source already exists: {name}")
        kind = body.get("kind", "webhook")
        if kind not in ("webhook", "github", "feishu"):
            raise HTTPException(status_code=422, detail="kind must be webhook|github|feishu")
        secret = body.get("secret")
        secret_hash = hash_secret(secret) if secret else None
        src = v2.source_repo.create(name, kind, body.get("config") or {}, secret_hash)
        return src

    @router.get("")
    def list_sources():
        return v2.source_repo.list_all()

    @router.get("/{name}")
    def get_source(name: str):
        src = v2.source_repo.get(name)
        if src is None:
            raise HTTPException(status_code=404, detail=f"Source '{name}' not found")
        return src

    @router.put("/{name}")
    def update_source(name: str, body: dict):
        if v2.source_repo.get(name) is None:
            raise HTTPException(status_code=404, detail=f"Source '{name}' not found")
        secret = body.get("secret")
        src = v2.source_repo.update(
            name, body.get("config") or {},
            secret_hash=hash_secret(secret) if secret else None,
            enabled=body.get("enabled"),
        )
        return src

    @router.delete("/{name}")
    def delete_source(name: str):
        if not v2.source_repo.delete(name):
            raise HTTPException(status_code=404, detail=f"Source '{name}' not found")
        return {"ok": True}

    @router.post("/{name}/webhook")
    def webhook(name: str, body: dict, x_source_secret: str | None = Header(default=None)):
        """入站 webhook:秘钥校验 → Trigger → gate → Eval → 立项/观察/拒绝。"""
        src = v2.source_repo.get(name)
        if src is None or not src["enabled"]:
            raise HTTPException(status_code=404, detail=f"Source '{name}' not found or disabled")
        if not verify_secret({}, v2.source_repo.get_secret_hash(name), x_source_secret):
            raise HTTPException(status_code=401, detail="invalid source secret")
        trigger = WebhookSource.parse(body)
        if not trigger.thread_key or not trigger.text:
            raise HTTPException(status_code=422, detail="thread_key/text 必填")
        try:
            result = v2.inbound_engine.handle(name, trigger)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return result

    # —— 观察模式面板(dsh: would-accept 只记录不执行,人工确认转正) ——
    @router.get("/observe/reviews")
    def observe_reviews(limit: int = 100):
        return v2.evaluation_repo.list_by_decision("would_accept", limit=limit)

    @router.post("/observe/{evaluation_id}/confirm")
    def observe_confirm(evaluation_id: str, body: dict | None = None):
        by = (body or {}).get("by", "human")
        try:
            return v2.inbound_engine.confirm(evaluation_id, confirmed_by=by)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e))

    return router
