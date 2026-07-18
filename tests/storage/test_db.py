import sqlite3


def test_init_db_creates_tables(tmp_db: sqlite3.Connection):
    cur = tmp_db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cur.fetchall()}
    assert {"runs", "run_events", "approvals"}.issubset(tables)


def test_init_db_idempotent(tmp_db: sqlite3.Connection):
    # 再跑一次 schema 不应报错
    from agentteam.storage.db import SCHEMA

    tmp_db.executescript(SCHEMA)
    tmp_db.commit()


def test_init_db_creates_parent_dir(tmp_path):
    from agentteam.storage.db import init_db

    nested = tmp_path / "nested" / "deep" / "test.db"
    conn = init_db(nested)
    assert nested.exists()
    conn.close()


def test_init_db_creates_teams_table(tmp_db: sqlite3.Connection):
    cur = tmp_db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cur.fetchall()}
    assert "teams" in tables


def test_init_db_creates_library_agents_table(tmp_db: sqlite3.Connection):
    cur = tmp_db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cur.fetchall()}
    assert "library_agents" in tables


def test_teams_table_columns(tmp_db: sqlite3.Connection):
    cur = tmp_db.execute("PRAGMA table_info(teams)")
    cols = {row[1] for row in cur.fetchall()}
    assert {"name", "description", "config", "created_at", "updated_at"}.issubset(cols)


def test_library_agents_table_columns(tmp_db: sqlite3.Connection):
    cur = tmp_db.execute("PRAGMA table_info(library_agents)")
    cols = {row[1] for row in cur.fetchall()}
    assert {"name", "config", "created_at", "updated_at"}.issubset(cols)
