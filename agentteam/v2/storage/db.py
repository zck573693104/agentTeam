"""v2 数据库:干净 schema 从 v1 起步(不带 v1 的 17 版迁移包袱)。

一次性建全量表(含 P2/P3 才启用的 threads/triggers/sources/evaluations/
subscriptions/attentions),后续演进用 PRAGMA user_version 迁移框架
(沿用 v1 已验证的幂等模式)。
"""
from __future__ import annotations

import sqlite3

from agentteam.logging_config import get_logger

logger = get_logger("v2.storage.db")

# 每个迁移: (版本号, [DDL])。v2 内核发布后按需追加,禁止改历史迁移。
MIGRATIONS: list[tuple[int, list[str]]] = [
    (1, [
        # —— Worker Pool(v2 压平组织:Router + 扁平工人池) ——
        """
        CREATE TABLE IF NOT EXISTS workers (
            name TEXT PRIMARY KEY,
            system_prompt TEXT NOT NULL DEFAULT '',
            model_json TEXT,
            tools_json TEXT NOT NULL DEFAULT '[]',
            skills_json TEXT NOT NULL DEFAULT '[]',
            approval_json TEXT NOT NULL DEFAULT '[]',
            max_iterations INTEGER NOT NULL DEFAULT 10,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        # —— Ticket(已授权工作单元) ——
        """
        CREATE TABLE IF NOT EXISTS tickets (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            task TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            worker_name TEXT,
            thread_id TEXT,
            mandate_json TEXT,
            result TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            ended_at TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status, created_at)",
        # —— Session(Ticket 的执行尝试;一个 ticket 可多次) ——
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            ticket_id TEXT NOT NULL,
            worker_name TEXT NOT NULL,
            workspace_dir TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            iterations INTEGER NOT NULL DEFAULT 0,
            max_iterations INTEGER NOT NULL DEFAULT 10,
            model_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_sessions_ticket ON sessions(ticket_id, created_at)",
        # —— messages(会话 transcript,v2 的 checkpoint) ——
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            tool_calls_json TEXT,
            tool_call_id TEXT,
            created_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id)",
        # —— events(统一事件流:生命周期+trace,SSE 回放源) ——
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT NOT NULL,
            session_id TEXT,
            type TEXT NOT NULL,
            actor TEXT NOT NULL DEFAULT 'system',
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_events_ticket ON events(ticket_id, id)",
        # —— Attention(P1 激活:审批/结构化提问,一次一个) ——
        """
        CREATE TABLE IF NOT EXISTS attentions (
            id TEXT PRIMARY KEY,
            ticket_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            type TEXT NOT NULL,              -- approval | question
            payload_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',   -- pending | decided
            decision TEXT,
            decided_by TEXT,
            reason TEXT,
            created_at TEXT NOT NULL,
            decided_at TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_attentions_status ON attentions(status, created_at)",
        # —— Thread/Trigger(P2/P3: 入站事件上下文单元) ——
        """
        CREATE TABLE IF NOT EXISTS threads (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            thread_key TEXT NOT NULL,
            title TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(source, thread_key)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS triggers (
            id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            actor TEXT NOT NULL,
            text TEXT NOT NULL,
            raw_json TEXT NOT NULL DEFAULT '{}',
            at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_triggers_thread ON triggers(thread_id, at)",
        # —— Subscription(P2: Ticket↔Thread 多对多,追问 fan-out) ——
        """
        CREATE TABLE IF NOT EXISTS subscriptions (
            ticket_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (ticket_id, thread_id)
        )
        """,
        # —— Source/Evaluation(P3: 入站源 + gate/Eval 决定,观察模式) ——
        """
        CREATE TABLE IF NOT EXISTS sources (
            name TEXT PRIMARY KEY,
            kind TEXT NOT NULL DEFAULT 'webhook',
            config_json TEXT NOT NULL DEFAULT '{}',
            secret_hash TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS evaluations (
            id TEXT PRIMARY KEY,
            trigger_id TEXT NOT NULL,
            gate_json TEXT NOT NULL DEFAULT '{}',
            decision TEXT NOT NULL,          -- accepted | rejected | would_accept
            reason TEXT,
            ticket_id TEXT,
            created_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_evaluations_decision ON evaluations(decision, created_at)",
        # —— Outbox(P2 移植: 先存后发,退避重试,幂等回执) ——
        """
        CREATE TABLE IF NOT EXISTS outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT NOT NULL,
            event TEXT NOT NULL,
            url TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            next_retry_at REAL NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            sent_at REAL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_outbox_due ON outbox(status, next_retry_at)",
        "CREATE INDEX IF NOT EXISTS idx_outbox_ticket ON outbox(ticket_id, id)",
    ]),
    # v2: WorkerDef 增加 MCP 挂载(presets 迁移需要;与 v1 MCPServer dict 同构)
    (2, ["ALTER TABLE workers ADD COLUMN mcp_json TEXT NOT NULL DEFAULT '[]'"]),
    # v3: 预算治理 + worktree + token 记账 + transcript 压缩
    (3, [
        "ALTER TABLE tickets ADD COLUMN repo_path TEXT",            # 关联 git 仓库(A2 worktree)
        "ALTER TABLE tickets ADD COLUMN budget_tokens INTEGER NOT NULL DEFAULT 0",  # 0 = 不限额
        "ALTER TABLE sessions ADD COLUMN tokens_in INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE sessions ADD COLUMN tokens_out INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE messages ADD COLUMN compacted INTEGER NOT NULL DEFAULT 0",     # chain-of-recall
    ]),
]


def init_db(db_path: str) -> sqlite3.Connection:
    """打开连接并应用全部幂等迁移。

    - WAL 模式: 读写并发友好
    - row_factory=Row: 列名访问
    - 返回的 Connection 与调用方提供的 lock 配合(见 BaseSqliteRepo)
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, ddls in MIGRATIONS:
        if version <= current:
            continue
        for ddl in ddls:
            conn.execute(ddl)
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
        logger.info("v2 db migrated to version %d (%s)", version, db_path)
    return conn
