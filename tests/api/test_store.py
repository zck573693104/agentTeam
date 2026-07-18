import pytest

from agentteam.api.store import TeamStore
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


def test_register_and_get():
    store = TeamStore()
    team = _make_team()
    store.register(team)
    assert store.get("dev") is team


def test_get_nonexistent_returns_none():
    store = TeamStore()
    assert store.get("nope") is None


def test_list_all_returns_all_registered():
    store = TeamStore()
    store.register(_make_team("a"))
    store.register(_make_team("b"))
    names = sorted(t.name for t in store.list_all())
    assert names == ["a", "b"]


def test_delete_removes_team():
    store = TeamStore()
    store.register(_make_team("dev"))
    assert store.delete("dev") is True
    assert store.get("dev") is None


def test_delete_nonexistent_returns_false():
    store = TeamStore()
    assert store.delete("nope") is False


def test_register_overwrites_existing():
    store = TeamStore()
    store.register(_make_team("dev"))
    team2 = _make_team("dev")
    team2.description = "updated"
    store.register(team2)
    assert store.get("dev").description == "updated"


def test_team_store_db_backed_register_persists(tmp_path):
    """DB-backed TeamStore: register 后,新实例同 DB 能 get 到。"""
    import sqlite3
    from agentteam.storage.db import init_db
    from agentteam.storage.teams import TeamRepo

    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    repo = TeamRepo(conn)
    store = TeamStore(repo=repo)
    team = _make_team("dev")
    store.register(team)
    # 新 store 同 DB,模拟重启
    store2 = TeamStore(repo=TeamRepo(conn))
    got = store2.get("dev")
    assert got is not None
    assert got.name == "dev"
    assert got.description == "test"
    conn.close()


def test_team_store_db_backed_loads_existing_on_init(tmp_path):
    """DB-backed TeamStore: 初始化时从 DB 加载已有 teams。"""
    import sqlite3
    from agentteam.storage.db import init_db
    from agentteam.storage.teams import TeamRepo

    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    repo = TeamRepo(conn)
    repo.upsert(_make_team("pre_existing"))
    # 新 store 初始化时应加载 pre_existing
    store = TeamStore(repo=repo)
    assert store.get("pre_existing") is not None
    assert "pre_existing" in [t.name for t in store.list_all()]
    conn.close()


def test_team_store_db_backed_delete_persists(tmp_path):
    """DB-backed TeamStore: delete 后,新实例同 DB 仍为空。"""
    import sqlite3
    from agentteam.storage.db import init_db
    from agentteam.storage.teams import TeamRepo

    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    repo = TeamRepo(conn)
    store = TeamStore(repo=repo)
    store.register(_make_team("dev"))
    assert store.delete("dev") is True
    # 新 store 同 DB,模拟重启
    store2 = TeamStore(repo=TeamRepo(conn))
    assert store2.get("dev") is None
    conn.close()


def test_team_store_no_repo_is_in_memory_only(tmp_path):
    """无 repo 参数:纯内存模式,不持久化(向后兼容)。"""
    store = TeamStore()
    store.register(_make_team("dev"))
    assert store.get("dev") is not None
    # 新 store 无 DB,模拟重启 —— 数据应丢失
    store2 = TeamStore()
    assert store2.get("dev") is None
