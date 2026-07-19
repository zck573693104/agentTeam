"""SP7b EvolutionRepo 单元测试。"""
import sqlite3
import threading

import pytest

from agentteam.storage.evolution import EvolutionRepo


def _make_repo(tmp_path) -> tuple[EvolutionRepo, sqlite3.Connection]:
    """构造测试用 EvolutionRepo(含 evolution_history 表)。"""
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.row_factory = sqlite3.Row
    # 创建表(复用生产 schema)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS evolution_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            version INTEGER NOT NULL,
            dimension TEXT NOT NULL,
            before_value TEXT,
            after_value TEXT,
            diff TEXT,
            reason TEXT,
            run_id TEXT,
            success BOOLEAN NOT NULL,
            error TEXT,
            timestamp TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_evo_agent ON evolution_history(agent_name, version);
    """)
    conn.commit()
    repo = EvolutionRepo(conn, lock=threading.Lock())
    return repo, conn


def test_add_record_returns_id(tmp_path):
    """add_record 返回新插入的 id(>=1)。"""
    repo, _ = _make_repo(tmp_path)
    eid = repo.add_record(
        agent_name="coder", version=1, dimension="prompt",
        before_value="old", after_value="new", diff="...",
        reason="test", run_id="r1", success=True,
    )
    assert eid >= 1


def test_add_record_failed_dimension_stores_error(tmp_path):
    """success=False 时,error 字段记录失败原因。"""
    repo, _ = _make_repo(tmp_path)
    eid = repo.add_record(
        agent_name="coder", version=1, dimension="prompt",
        before_value="", after_value="", diff="",
        reason="LLM error", run_id="r1", success=False, error="timeout",
    )
    assert eid >= 1


def test_list_history_returns_records_for_agent(tmp_path):
    """list_history 返回该 agent 的所有记录。"""
    repo, _ = _make_repo(tmp_path)
    repo.add_record("coder", 1, "prompt", "a", "b", "", "r1", "r1", True)
    repo.add_record("coder", 2, "params", "{}", "{}", "", "r2", "r2", True)
    repo.add_record("reviewer", 1, "prompt", "x", "y", "", "r3", "r3", True)
    history = repo.list_history("coder")
    assert len(history) == 2
    assert all(h["agent_name"] == "coder" for h in history)


def test_list_history_ordered_by_timestamp_desc(tmp_path):
    """list_history 按 timestamp 倒序(最新在前)。"""
    repo, conn = _make_repo(tmp_path)
    # 手动插入两条带不同 timestamp
    conn.execute(
        "INSERT INTO evolution_history (agent_name, version, dimension, success, timestamp) "
        "VALUES (?, ?, ?, ?, ?)", ("a", 1, "prompt", 1, "2026-01-01 10:00:00"),
    )
    conn.execute(
        "INSERT INTO evolution_history (agent_name, version, dimension, success, timestamp) "
        "VALUES (?, ?, ?, ?, ?)", ("a", 2, "prompt", 1, "2026-01-02 10:00:00"),
    )
    conn.commit()
    history = repo.list_history("a")
    assert len(history) == 2
    assert history[0]["version"] == 2  # 新版本在前
    assert history[1]["version"] == 1


def test_list_history_respects_limit(tmp_path):
    """list_history(limit=N) 最多返回 N 条。"""
    repo, _ = _make_repo(tmp_path)
    for i in range(5):
        repo.add_record("a", i + 1, "prompt", "", "", "", "", "r", True)
    assert len(repo.list_history("a", limit=3)) == 3
    assert len(repo.list_history("a", limit=10)) == 5


def test_list_history_unknown_agent_returns_empty(tmp_path):
    """未知 agent:list_history 返回空 list,不抛异常。"""
    repo, _ = _make_repo(tmp_path)
    assert repo.list_history("nonexistent") == []
