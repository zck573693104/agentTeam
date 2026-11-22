"""组织架构树 API(P6: 任意深度层级,动态增删改;工人挂载在节点上)。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agentteam.v2.api.deps import V2Components


def org_router(v2: V2Components) -> APIRouter:
    router = APIRouter(prefix="/org", tags=["v2-org"])

    @router.get("")
    def get_tree():
        """整棵组织树(嵌套 children,深度无上限)+ 各节点挂载的工人。"""
        workers_by_node: dict[str, list[dict]] = {}
        for w in v2.worker_repo.list_all():
            if w.org_node_id:
                workers_by_node.setdefault(w.org_node_id, []).append(w.to_dict())
        return {"tree": v2.org_repo.tree(), "workers_by_node": workers_by_node}

    @router.post("")
    def create_node(body: dict):
        """新增节点:parent_id 为空 = 新根部门;非空 = 在任意节点下加子级(动态加深)。"""
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="name is required")
        parent_id = body.get("parent_id") or None
        if parent_id and v2.org_repo.get(parent_id) is None:
            raise HTTPException(status_code=422, detail=f"parent node '{parent_id}' not found")
        kind = (body.get("kind") or ("department" if not parent_id else "group")).strip()
        node = v2.org_repo.create(
            name, parent_id,
            node_id=body.get("id"), kind=kind,
            position=int(body.get("position") or 0),
        )
        return node

    @router.patch("/{node_id}")
    def update_node(node_id: str, body: dict):
        """改名/改标签/移动(parent_id,带环检测:不能挂到自己的子孙下)。"""
        if v2.org_repo.get(node_id) is None:
            raise HTTPException(status_code=404, detail=f"org node '{node_id}' not found")
        kwargs: dict = {}
        if "name" in body:
            if not (body.get("name") or "").strip():
                raise HTTPException(status_code=422, detail="name cannot be empty")
            kwargs["name"] = body["name"].strip()
        if "kind" in body:
            kwargs["kind"] = (body.get("kind") or "group").strip()
        if "position" in body:
            kwargs["position"] = int(body.get("position") or 0)
        if "parent_id" in body:
            parent_id = body.get("parent_id") or None
            if parent_id and v2.org_repo.get(parent_id) is None:
                raise HTTPException(status_code=422, detail=f"parent node '{parent_id}' not found")
            kwargs["parent_id"] = parent_id
        try:
            node = v2.org_repo.update(node_id, **kwargs)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return node

    @router.delete("/{node_id}")
    def delete_node(node_id: str):
        """删除节点:有子节点或有挂载工人 → 409(先处理子树/工人)。"""
        node = v2.org_repo.get(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail=f"org node '{node_id}' not found")
        has_children = node_id in v2.org_repo.children_count_map()
        has_workers = any(w.org_node_id == node_id
                          for w in v2.worker_repo.list_all())
        try:
            v2.org_repo.delete(node_id, has_children=has_children, has_workers=has_workers)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"ok": True}

    return router
