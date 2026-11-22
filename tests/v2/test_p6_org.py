"""P6 组织架构树测试:任意深度层级/动态加子级/环检测/删除守卫/工人挂载。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentteam.v2.api.deps import create_v2_components
from agentteam.v2.api.router import build_v2_router
from agentteam.v2.domain.worker import WorkerDef
from agentteam.v2.storage.db import init_db
from agentteam.v2.storage.org import OrgRepo
from tests.conftest import FakeLLM, FakeModelProvider


@pytest.fixture
def org(tmp_path: Path) -> OrgRepo:
    conn = init_db(str(tmp_path / "org.db"))
    repo = OrgRepo(conn)
    yield repo
    conn.close()


# —— 树 CRUD 与任意深度 ——
def test_tree_any_depth(org: OrgRepo):
    """动态加深:沿一个节点一路加子级到第 5 层,tree() 结构正确。"""
    org.create("公司", None, node_id="n0", kind="department")
    parent = "n0"
    for i in range(1, 5):  # 层级 1~5
        nid = f"n{i}"
        org.create(f"L{i}", parent, node_id=nid)
        parent = nid
    tree = org.tree()
    assert len(tree) == 1 and tree[0]["id"] == "n0"
    node = tree[0]
    depth = 0
    while node["children"]:
        node = node["children"][0]
        depth += 1
    assert depth == 4 and node["id"] == "n4"


def test_move_cycle_detection(org: OrgRepo):
    org.create("部", None, node_id="d", kind="department")
    org.create("组", "d", node_id="g")
    org.create("小组", "g", node_id="t")
    # 把部门移到它自己的子孙下 → 成环,拒绝
    with pytest.raises(ValueError, match="环"):
        org.update("d", parent_id="t")
    # 移到自身 → 拒绝
    with pytest.raises(ValueError, match="自身"):
        org.update("d", parent_id="d")
    # 合法移动:小组挪回根
    moved = org.update("t", parent_id=None)
    assert moved["parent_id"] is None


def test_rename_and_kind(org: OrgRepo):
    org.create("旧名", None, node_id="x")
    updated = org.update("x", name="新名", kind="team")
    assert updated["name"] == "新名" and updated["kind"] == "team"


def test_delete_guards(org: OrgRepo):
    org.create("部", None, node_id="d", kind="department")
    org.create("组", "d", node_id="g")
    # 有子级 → 拒绝
    with pytest.raises(ValueError, match="子节点"):
        org.delete("d", has_children=True, has_workers=False)
    # 无子级无工人 → 可删
    assert org.delete("g", has_children=False, has_workers=False)


# —— API 层:工人挂载 + 树接口 + 动态加深 ——
def _client(tmp_path: Path):
    provider = FakeModelProvider({"qwen-max": FakeLLM()})
    v2 = create_v2_components(db_path=str(tmp_path / "v2.db"),
                              workspace_root=tmp_path / "ws",
                              model_provider=provider)
    app = FastAPI()
    app.include_router(build_v2_router(v2), prefix="/api")
    return TestClient(app), v2


def test_worker_attach_and_org_api(tmp_path: Path):
    client, v2 = _client(tmp_path)
    v2.org_repo.create("PMS部门", None, node_id="org_pms", kind="department")
    v2.org_repo.create("操作组", "org_pms", node_id="org_pms_ops")
    # 挂载到存在的节点 → 成功
    r = client.post("/api/workers", json=WorkerDef(
        name="w1", org_node_id="org_pms_ops").to_dict())
    assert r.status_code == 200
    # 挂载到不存在的节点 → 422
    r2 = client.post("/api/workers", json=WorkerDef(
        name="w2", org_node_id="org_ghost").to_dict())
    assert r2.status_code == 422
    # 树接口:节点 + 挂载映射
    body = client.get("/api/org").json()
    assert body["tree"][0]["id"] == "org_pms"
    assert body["tree"][0]["children"][0]["id"] == "org_pms_ops"
    assert [w["name"] for w in body["workers_by_node"]["org_pms_ops"]] == ["w1"]
    # 删除守卫:组上有工人 → 409
    assert client.delete("/api/org/org_pms_ops").status_code == 409
    # 动态加子级(第 3 层) → 树深度体现
    r4 = client.post("/api/org", json={"name": "夜班小组", "kind": "team",
                                       "parent_id": "org_pms_ops"})
    assert r4.status_code == 200
    grp = client.get("/api/org").json()["tree"][0]["children"][0]
    assert grp["children"][0]["name"] == "夜班小组"
    # 移动成环 → 409
    r5 = client.patch("/api/org/org_pms",
                      json={"parent_id": grp["children"][0]["id"]})
    assert r5.status_code == 409
    v2.conn.close()


def test_worker_persists_org_node_id(tmp_path: Path):
    client, v2 = _client(tmp_path)
    v2.org_repo.create("应用部", None, node_id="org_app", kind="department")
    client.post("/api/workers", json=WorkerDef(
        name="app_finance", org_node_id="org_app").to_dict())
    w = v2.worker_repo.get("app_finance")
    assert w.org_node_id == "org_app"          # 缓存
    got = client.get("/api/workers/app_finance").json()
    assert got["org_node_id"] == "org_app"     # API
    v2.conn.close()
