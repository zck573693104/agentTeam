"""并发 BUG 修复测试:BUG-01/02/03 + BUG-11 library API 字段契约。

覆盖:
- BUG-01: TeamStore POST /api/teams check-then-act 竞态 → register_if_absent 原子方法
- BUG-02: AgentLibrary POST /api/library/agents 竞态导致 500 → 路由返回 400
- BUG-03: TeamStore/AgentLibrary cache-DB 不一致 → DB 先内存后,DB 失败保留内存
- BUG-11: AgentDict 缺 children/ref/mcp_servers 字段 → 补字段 + list_agents 全字段
"""
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentteam.api.routes.teams import teams_router
from agentteam.api.server import create_app
from agentteam.api.store import TeamStore
from agentteam.domain.agent import Agent
from agentteam.domain.library import AgentLibrary
from agentteam.domain.team import Leader, Team
from agentteam.domain.worker import Worker
from agentteam.models.provider import ModelRef


def _make_team(name="dev") -> Team:
    return Team(
        name=name,
        description="test",
        leader=Leader(system_prompt="x"),
        workers=[Worker(name="w1", role="r", description="", system_prompt="x")],
        default_model=ModelRef(provider="qwen", name="qwen-max"),
    )


class _FailingDeleteRepo:
    """模拟 DB delete 抛异常的 repo。

    upsert 正常(让 register 成功),delete 抛 RuntimeError。
    """

    def __init__(self) -> None:
        self._data: dict[str, Team] = {}

    def list_all(self):
        return list(self._data.values())

    def upsert(self, team) -> None:
        self._data[team.name] = team

    def delete(self, name) -> bool:
        raise RuntimeError("DB delete failed")


class _FailingUpsertRepo:
    """模拟 DB upsert 抛异常的 repo。"""

    def __init__(self) -> None:
        self._data: dict[str, Team] = {}

    def list_all(self):
        return list(self._data.values())

    def upsert(self, team) -> None:
        raise RuntimeError("DB upsert failed")

    def delete(self, name) -> bool:
        self._data.pop(name, None)
        return True


# ---------------------------------------------------------------------------
# BUG-01: TeamStore.register_if_absent 原子性
# ---------------------------------------------------------------------------


def test_team_store_register_if_absent_returns_true_for_new():
    """register_if_absent 新 team 返回 True,再次注册同名返回 False。"""
    store = TeamStore()
    assert store.register_if_absent(_make_team("dev")) is True
    assert store.register_if_absent(_make_team("dev")) is False
    assert store.get("dev") is not None


def test_team_store_register_if_absent_atomic():
    """并发 register_if_absent 同名 team,只有一个返回 True(BUG-01)。

    用 Barrier 让所有线程同时进入 register_if_absent,最大化竞态窗口。
    无锁实现下可能出现多个 True(check-then-act 竞态);加锁后恰好一个 True。
    """
    store = TeamStore()
    n = 30
    barrier = threading.Barrier(n)
    results: list[bool] = []
    lock = threading.Lock()

    def reg():
        barrier.wait()
        team = _make_team("race")
        ok = store.register_if_absent(team)
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=reg) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1
    assert results.count(False) == n - 1
    assert store.get("race") is not None


def test_team_register_concurrent_route_returns_400_not_overwrite():
    """并发 POST /api/teams 同名 team:恰好一个 200,其余 400,无 500(BUG-01 路由层)。"""
    store = TeamStore()
    app = FastAPI()
    app.include_router(teams_router(store))
    n = 20
    barrier = threading.Barrier(n)
    statuses: list[int] = []

    def post_team():
        barrier.wait()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/teams",
            json={
                "name": "race",
                "description": "x",
                "leader": {"name": "leader", "role": "主管", "system_prompt": "x"},
                "workers": [
                    {"name": "w1", "role": "r", "description": "", "system_prompt": "x"}
                ],
                "default_model": {"provider": "qwen", "name": "qwen-max"},
                "skills": [],
            },
        )
        with lock:
            statuses.append(resp.status_code)

    lock = threading.Lock()
    threads = [threading.Thread(target=post_team) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert statuses.count(200) == 1
    assert statuses.count(400) == n - 1
    assert 500 not in statuses


# ---------------------------------------------------------------------------
# BUG-03: TeamStore cache-DB 一致性(DB 失败保留内存)
# ---------------------------------------------------------------------------


def test_team_store_delete_db_failure_keeps_cache():
    """DB delete 失败时,内存缓存保留该 team(BUG-03 delete)。

    修复前:先 del cache 再 repo.delete → cache 已空但 DB 还在(重启复活)。
    修复后:先 repo.delete(抛异常)→ 不动 cache → cache 保留,保持一致。
    """
    store = TeamStore(repo=_FailingDeleteRepo())
    store.register(_make_team("dev"))
    assert store.get("dev") is not None

    with pytest.raises(RuntimeError, match="DB delete failed"):
        store.delete("dev")

    # DB delete 失败 → 内存必须保留 team,避免状态分裂
    assert store.get("dev") is not None


def test_team_store_register_db_failure_keeps_cache_clean():
    """DB upsert 失败时,内存不写入(BUG-03 register)。

    修复前:先写 cache 再 repo.upsert → cache 有但 DB 无(重启丢失)。
    修复后:先 repo.upsert(抛异常)→ 不动 cache → cache 无,保持一致。
    """
    store = TeamStore(repo=_FailingUpsertRepo())
    with pytest.raises(RuntimeError, match="DB upsert failed"):
        store.register(_make_team("dev"))
    # DB 失败 → 内存不应有该 team
    assert store.get("dev") is None


def test_team_store_update_db_failure_keeps_old_cache():
    """DB upsert 失败时,内存保留旧 team(BUG-03 update)。

    修复前:先覆盖 cache 再 repo.upsert → cache 是新值但 DB 是旧值。
    修复后:先 repo.upsert(抛异常)→ 不动 cache → cache 仍是旧值。
    """
    store = TeamStore()  # 纯内存,先放一个旧 team
    old = _make_team("dev")
    old.description = "old"
    store.register(old)

    # 切到会失败的 repo 再 update —— 但 repo 在 __init__ 注入,这里换一种方式:
    # 直接构造带失败 repo 的 store,先 register(成功),再 update(失败)
    failing_store = TeamStore(repo=_FailingUpsertRepo())
    # 用纯内存 register 绕过失败 repo 放入旧值
    failing_store._cache["dev"] = old

    new_team = _make_team("dev")
    new_team.description = "new"
    with pytest.raises(RuntimeError, match="DB upsert failed"):
        failing_store.update(new_team)
    # DB 失败 → 内存仍是旧值
    assert failing_store.get("dev").description == "old"


# ---------------------------------------------------------------------------
# BUG-02: AgentLibrary POST /api/library/agents 竞态导致 500
# ---------------------------------------------------------------------------


def test_library_register_race_returns_400_not_500(monkeypatch):
    """模拟竞态:路由 get 检查通过(看到不存在),但 register 时 agent 已存在。

    修复前:library.register 抛 ValueError 未捕获 → 500。
    修复后:路由用 register_if_absent(或捕获 ValueError)→ 400。

    用 monkeypatch 让 library.get 恒返回 None,模拟"两个并发请求都通过 get 检查"
    的中间态:第一个请求已 register,第二个请求的 get 仍看到 None。
    """
    lib = AgentLibrary()
    # 预置 agent(模拟另一个并发请求已经 register 成功)
    lib.register(Agent(name="dup", role="worker"))
    # 让路由的 get 检查看到"不存在",模拟竞态窗口
    monkeypatch.setattr(lib, "get", lambda name: None)

    app = create_app(agent_library=lib, web_dist=None)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post("/api/library/agents", json={"name": "dup", "role": "worker"})
    assert resp.status_code == 400, f"expected 400, got {resp.status_code}"
    assert resp.status_code != 500


def test_library_register_concurrent_returns_400_not_500():
    """并发 POST /api/library/agents 同名 agent:恰好一个 200,其余 400,无 500(BUG-02)。

    每个线程用自己的 TestClient(独立 portal 线程)共享同一 app/lib,实现真并发。
    修复前:check-then-act 竞态 → 第二个 register 抛 ValueError 未捕获 → 500。
    修复后:register_if_absent 原子 → 第二个返回 False → 400。
    """
    lib = AgentLibrary()
    app = create_app(agent_library=lib, web_dist=None)
    n = 20
    barrier = threading.Barrier(n)
    statuses: list[int] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def post_agent():
        try:
            barrier.wait()
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/library/agents",
                json={"name": "dup", "role": "worker"},
            )
            with lock:
                statuses.append(resp.status_code)
        except BaseException as e:  # noqa: BLE001
            with lock:
                errors.append(e)

    threads = [threading.Thread(target=post_agent) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"threads crashed: {errors}"
    assert statuses.count(200) == 1, f"expected exactly one 200, got {statuses}"
    assert 500 not in statuses, f"race produced 500: {statuses}"
    assert all(c in (200, 400) for c in statuses), f"unexpected status: {statuses}"
    assert statuses.count(400) == n - 1


# ---------------------------------------------------------------------------
# BUG-11 + Arch #3: AgentDict 缺 children/ref/mcp_servers + list_agents 全字段
# ---------------------------------------------------------------------------


def test_library_agent_dict_accepts_children_ref_mcp_servers():
    """POST 带 children/ref/mcp_servers 字段 → 200,GET 返回完整字段(BUG-11)。"""
    lib = AgentLibrary()
    app = create_app(agent_library=lib, web_dist=None)
    client = TestClient(app)

    payload = {
        "name": "sup",
        "role": "supervisor",
        "system_prompt": "lead prompt",
        "tools": [],
        "max_iterations": 10,
        "model": None,
        "approval_policy": None,
        "children": [{"name": "w", "role": "worker", "system_prompt": "do work"}],
        "ref": None,
        "mcp_servers": [{"name": "git", "command": "git-mcp", "args": [], "env": {}}],
    }
    resp = client.post("/api/library/agents", json=payload)
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "sup"

    # GET 列表返回完整字段
    resp = client.get("/api/library/agents")
    assert resp.status_code == 200
    agents = resp.json()
    sup = [a for a in agents if a["name"] == "sup"][0]
    # children 字段保留
    assert len(sup["children"]) == 1
    assert sup["children"][0]["name"] == "w"
    assert sup["children"][0]["role"] == "worker"
    # mcp_servers 字段保留
    assert len(sup["mcp_servers"]) == 1
    assert sup["mcp_servers"][0]["name"] == "git"
    assert sup["mcp_servers"][0]["command"] == "git-mcp"
    # ref 字段保留(此处为 None)
    assert "ref" in sup


def test_library_agent_dict_accepts_ref_field():
    """POST 带 ref=library:other → 200,GET 返回 ref 字段(BUG-11 ref 契约)。"""
    lib = AgentLibrary()
    # 先放一个模板 agent 供 ref 引用
    lib.register(Agent(name="template", role="worker", system_prompt="tmpl"))
    app = create_app(agent_library=lib, web_dist=None)
    client = TestClient(app)

    resp = client.post(
        "/api/library/agents",
        json={
            "name": "alias",
            "role": "worker",
            "ref": "library:template",
        },
    )
    assert resp.status_code == 200, resp.text

    agents = client.get("/api/library/agents").json()
    alias = [a for a in agents if a["name"] == "alias"][0]
    assert alias["ref"] == "library:template"


def test_library_list_agents_returns_full_fields():
    """list_agents 返回完整字段(name/role/system_prompt/tools/max_iterations/model/
    approval_policy/children/ref/mcp_servers),客户端能看到全貌(Arch #3)。"""
    lib = AgentLibrary()
    app = create_app(agent_library=lib, web_dist=None)
    client = TestClient(app)

    # 注册一个带完整字段的 agent
    resp = client.post(
        "/api/library/agents",
        json={
            "name": "full",
            "role": "supervisor",
            "system_prompt": "lead",
            "tools": [],
            "max_iterations": 5,
            "model": {"provider": "qwen", "name": "qwen-max"},
            "approval_policy": {"level": "step"},
            "children": [{"name": "c", "role": "worker"}],
            "mcp_servers": [{"name": "s", "command": "s-mcp"}],
        },
    )
    assert resp.status_code == 200, resp.text

    agents = client.get("/api/library/agents").json()
    full = [a for a in agents if a["name"] == "full"][0]
    # 所有字段都在
    expected_keys = {
        "name", "role", "system_prompt", "tools", "max_iterations",
        "model", "approval_policy", "children", "ref", "mcp_servers",
    }
    assert expected_keys.issubset(set(full.keys())), f"missing keys: {expected_keys - set(full.keys())}"
    assert full["system_prompt"] == "lead"
    assert full["max_iterations"] == 5
    assert full["model"]["provider"] == "qwen"
    assert full["approval_policy"]["level"] == "step"
    assert len(full["children"]) == 1
    assert len(full["mcp_servers"]) == 1


# ---------------------------------------------------------------------------
# BUG-01 辅助:register_if_absent 在 DB-backed 模式下也原子
# ---------------------------------------------------------------------------


def test_team_store_register_if_absent_db_backed_atomic(tmp_path):
    """DB-backed TeamStore: 并发 register_if_absent 同名 team,只一个 True,DB 只一条。"""
    import sqlite3
    from agentteam.storage.db import init_db
    from agentteam.storage.teams import TeamRepo

    db_path = tmp_path / "race.db"
    conn = init_db(db_path)
    repo = TeamRepo(conn)
    store = TeamStore(repo=repo)

    n = 20
    barrier = threading.Barrier(n)
    results: list[bool] = []
    lock = threading.Lock()

    def reg():
        barrier.wait()
        ok = store.register_if_absent(_make_team("race"))
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=reg) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1
    assert results.count(False) == n - 1
    # DB 只有一条记录
    assert len(repo.list_all()) == 1
    conn.close()
