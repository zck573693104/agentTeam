import sqlite3


def test_init_db_creates_tables(tmp_db: sqlite3.Connection):
    cur = tmp_db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cur.fetchall()}
    assert {"runs", "run_events", "approvals"}.issubset(tables)


def test_init_db_idempotent(tmp_db: sqlite3.Connection):
    # 再跑一次 migration 不应报错且不重复执行(user_version 已标记)
    from agentteam.storage.db import run_migrations

    applied = run_migrations(tmp_db)
    assert applied == 0  # 所有 migration 已应用,无新增


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


def test_library_agents_has_version_column(tmp_db: sqlite3.Connection):
    """v3 migration 应给 library_agents 加 version 列。"""
    cur = tmp_db.execute("PRAGMA table_info(library_agents)")
    cols = {row[1] for row in cur.fetchall()}
    assert "version" in cols


def test_evolution_history_table_exists(tmp_db: sqlite3.Connection):
    """v2 migration 应创建 evolution_history 表。"""
    cur = tmp_db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cur.fetchall()}
    assert "evolution_history" in tables


def test_user_version_set_after_init(tmp_db: sqlite3.Connection):
    """init_db 完成后 PRAGMA user_version 应等于 MIGRATIONS 最大版本号。"""
    from agentteam.storage.db import MIGRATIONS

    cur = tmp_db.execute("PRAGMA user_version")
    version = cur.fetchone()[0]
    assert version == max(m[0] for m in MIGRATIONS)


def test_run_migrations_skips_already_applied(tmp_db: sqlite3.Connection):
    """已应用的 migration 重复调用不执行。"""
    from agentteam.storage.db import run_migrations

    # tmp_db fixture 已通过 init_db 应用了所有 migration
    applied = run_migrations(tmp_db)
    assert applied == 0


def test_run_migrations_partial_applies_remaining(tmp_path):
    """从中间版本开始,只应用剩余 migration。"""
    from agentteam.storage.db import init_db, run_migrations, MIGRATIONS

    db_path = tmp_path / "partial.db"
    conn = init_db(db_path)
    max_version = max(m[0] for m in MIGRATIONS)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == max_version
    # 模拟回滚到 v1(仅 user_version;表结构仍在)
    conn.execute(f"PRAGMA user_version = 1")
    # 再跑应只应用 v2/v3/v4
    applied = run_migrations(conn)
    assert applied == max_version - 1
    conn.close()


def test_migration_handles_legacy_db_without_user_version(tmp_path):
    """迁移框架引入前的旧库(user_version=0)应能正确升级。

    模拟:手动建表(无 user_version)→ 调 run_migrations → 所有 migration 重跑
    (因 idempotent 不报错)→ user_version 更新到最新。
    """
    from agentteam.storage.db import run_migrations, MIGRATIONS

    conn = sqlite3.connect(":memory:")
    # 手动建一个无 user_version 的旧库(只建 runs 表)
    conn.execute("""
        CREATE TABLE runs (
            id TEXT PRIMARY KEY, team_name TEXT, task TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT, updated_at TEXT, ended_at TEXT,
            total_tokens INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0

    # 跑 migration:应补齐所有缺失的表/列/索引
    applied = run_migrations(conn)
    assert applied == len(MIGRATIONS)  # 全部应用
    # 验证表存在
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert {"runs", "run_events", "approvals", "teams",
            "library_agents", "evolution_history"}.issubset(tables)
    # 验证 user_version 已更新
    assert conn.execute("PRAGMA user_version").fetchone()[0] == max(m[0] for m in MIGRATIONS)
    conn.close()
