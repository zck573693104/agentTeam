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
from tests.v2.conftest import wait_ticket_terminal


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
    v2.scheduler.start()  # 涉及工单执行的测试需要调度器;服务内由 lifespan 调用
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
    v2.scheduler.shutdown(wait=True)
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
    v2.scheduler.shutdown(wait=True)
    v2.conn.close()


# —— P6b: 树指导干活(Router 组织感知 / 按组派工 / 工人组织身份) ——
def _seed_org(v2):
    v2.org_repo.create("PMS部门", None, node_id="org_pms", kind="department")
    v2.org_repo.create("操作组", "org_pms", node_id="org_pms_ops")
    v2.org_repo.create("报表组", "org_pms", node_id="org_pms_report")
    for name, nid in (("pms_ops", "org_pms_ops"), ("pms_report", "org_pms_report"),
                      ("pms_lead", "org_pms")):
        v2.worker_repo.upsert(WorkerDef(name=name, org_node_id=nid))


def test_router_prompt_contains_org_digest(tmp_path: Path):
    """Router 派工决策 prompt 注入组织架构摘要(按树路由)。"""
    from langchain_core.messages import AIMessage

    from agentteam.v2.kernel.router import RouterDecision

    client, v2 = _client(tmp_path)
    _seed_org(v2)
    main = v2.model_provider.get_llm(v2.default_model)
    main.set_structured_responses([RouterDecision(action="reply", reply="demo")])
    main.set_invoke_responses([AIMessage(content="done")])
    client.post("/api/requests", json={"text": "随便什么"})
    prompts = [c["messages"] for c in main.invoked_structured]
    assert any("【组织架构】" in str(p) and "报表组 [pms_report]" in str(p) for p in prompts)
    v2.scheduler.shutdown(wait=True)
    v2.conn.close()


def test_submit_by_org_node_picks_in_subtree(tmp_path: Path):
    """按组派工:org_node=org_pms → Router 只在子树成员中选人;空子树 → 422。"""
    from langchain_core.messages import AIMessage

    from agentteam.v2.kernel.router import RouterDecision

    client, v2 = _client(tmp_path)
    _seed_org(v2)
    main = v2.model_provider.get_llm(v2.default_model)
    main.set_structured_responses([
        RouterDecision(action="dispatch", worker="pms_report", goal="出月报"),
    ])
    main.set_invoke_responses([AIMessage(content="done")])
    r = client.post("/api/requests", json={"text": "给 PMS 部门出月报",
                                           "org_node": "org_pms"})
    assert r.status_code == 200
    tid = r.json()["ticket_id"]
    t = wait_ticket_terminal(v2, tid)
    assert t.worker_name == "pms_report"
    # 组织外工人不在候选 → Router 兜底也只在子树内
    assert t.worker_name in {"pms_ops", "pms_report", "pms_lead"}
    # 空子树节点 → 422
    v2.org_repo.create("空组", "org_pms", node_id="org_pms_empty")
    r2 = client.post("/api/requests", json={"text": "x", "org_node": "org_pms_empty"})
    assert r2.status_code == 422
    # 不存在的节点 → 422
    r3 = client.post("/api/requests", json={"text": "x", "org_node": "org_ghost"})
    assert r3.status_code == 422
    v2.scheduler.shutdown(wait=True)
    v2.conn.close()


def test_worker_session_knows_org_identity(tmp_path: Path):
    """工人会话 system prompt 注入组织身份(路径 + 部门负责人)。"""
    from langchain_core.messages import AIMessage

    client, v2 = _client(tmp_path)
    _seed_org(v2)
    main = v2.model_provider.get_llm(v2.default_model)
    main.set_invoke_responses([AIMessage(content="done")])
    tid = v2.scheduler.submit_request("x", worker_name="pms_ops")["ticket_id"]
    wait_ticket_terminal(v2, tid)
    sys = main.invoked_messages[0][0].content
    assert "【组织身份】" in sys
    assert "PMS部门 › 操作组" in sys
    assert "负责人: pms_lead" in sys
    v2.scheduler.shutdown(wait=True)
    v2.conn.close()
