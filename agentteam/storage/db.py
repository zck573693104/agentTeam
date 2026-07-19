from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id           TEXT PRIMARY KEY,
    team_name    TEXT NOT NULL,
    task         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    ended_at     TEXT,
    total_tokens INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS run_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    actor       TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}',
    duration_ms INTEGER,
    tokens      INTEGER,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_run_events_run_id ON run_events(run_id);

CREATE TABLE IF NOT EXISTS approvals (
    id            TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    requested_at  TEXT NOT NULL,
    decided_at    TEXT,
    decider       TEXT,
    reason        TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS teams (
    name        TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    config      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS library_agents (
    name        TEXT PRIMARY KEY,
    config      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


def init_db(path: str | Path = "data/agentteam.db") -> sqlite3.Connection:
    """初始化 SQLite 数据库，创建 schema，返回连接。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: the connection may be shared with SqliteSaver,
    # which writes checkpoints from worker threads and serializes access via
    # its own lock. Safe for single-threaded use as well.
    conn = sqlite3.connect(str(p), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    # SP7b: evolution_history 表
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS evolution_history (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name   TEXT NOT NULL,
            version      INTEGER NOT NULL,
            dimension    TEXT NOT NULL,
            before_value TEXT,
            after_value  TEXT,
            diff         TEXT,
            reason       TEXT,
            run_id       TEXT,
            success      BOOLEAN NOT NULL,
            error        TEXT,
            timestamp    TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_evo_agent ON evolution_history(agent_name, version);
    """)
    # library_agents 加 version 列(若不存在)
    try:
        conn.execute("ALTER TABLE library_agents ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
    except sqlite3.OperationalError:
        pass  # 列已存在
    conn.commit()
    return conn
