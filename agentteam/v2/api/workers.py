"""Worker Pool CRUD(工人池,v2 的"团队配置")。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agentteam.v2.api.deps import V2Components
from agentteam.v2.domain.worker import WorkerDef


def workers_router(v2: V2Components) -> APIRouter:
    router = APIRouter(prefix="/workers", tags=["v2-workers"])

    def _validate_org_node(node_id: str | None) -> None:
        """P6: 工人挂载的组织节点必须存在(空 = 未挂载,合法)。"""
        if node_id and v2.org_repo.get(node_id) is None:
            raise HTTPException(status_code=422, detail=f"org node '{node_id}' not found")

    @router.get("")
    def list_workers():
        return [w.to_dict() for w in v2.worker_repo.list_all()]

    @router.post("")
    def create_worker(body: dict):
        try:
            w = WorkerDef.from_dict(body)
        except KeyError as e:
            raise HTTPException(status_code=422, detail=f"missing field: {e}")
        if v2.worker_repo.get(w.name) is not None:
            raise HTTPException(status_code=400, detail=f"Worker already exists: {w.name}")
        _validate_org_node(w.org_node_id)
        v2.worker_repo.upsert(w)
        noted = v2.scheduler.on_pool_changed()  # 配置热更新 → 在途工单授权注记
        return {"name": w.name, "policy_expiry_noted": noted}

    @router.get("/{name}")
    def get_worker(name: str):
        w = v2.worker_repo.get(name)
        if w is None:
            raise HTTPException(status_code=404, detail=f"Worker '{name}' not found")
        return w.to_dict()

    @router.put("/{name}")
    def update_worker(name: str, body: dict):
        if v2.worker_repo.get(name) is None:
            raise HTTPException(status_code=404, detail=f"Worker '{name}' not found")
        if body.get("name") != name:
            raise HTTPException(status_code=400, detail="body.name must match URL")
        try:
            w = WorkerDef.from_dict(body)
        except KeyError as e:
            raise HTTPException(status_code=422, detail=f"missing field: {e}")
        _validate_org_node(w.org_node_id)
        v2.worker_repo.upsert(w)
        noted = v2.scheduler.on_pool_changed()
        return {"name": w.name, "policy_expiry_noted": noted}

    @router.delete("/{name}")
    def delete_worker(name: str):
        if not v2.worker_repo.delete(name):
            raise HTTPException(status_code=404, detail=f"Worker '{name}' not found")
        v2.scheduler.on_pool_changed()
        return {"ok": True}

    return router
