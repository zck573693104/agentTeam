from __future__ import annotations

import sqlite3
from pathlib import Path

# ---- Schema 迁移框架 ----
#
# 用 PRAGMA user_version(SQLite 原生整型版本号)追踪 schema 版本。
# 每个 migration 是 (version, description, sql) 三元组,init_db 按版本号顺序执行
# 所有 version > current_user_version 的 migration,然后更新 user_version。
#
# 设计要点:
# - migration sql 应尽量幂等(CREATE ... IF NOT EXISTS),以兼容"迁移框架引入前已存在"
#   的旧数据库(user_version=0,所有 migration 重跑)。新增非幂等 DDL(ALTER ADD COLUMN
#   除外,SQLite 不支持 IF NOT EXISTS)需要用 try/except 包裹。
# - migration 之间显式声明版本号(1,2,3...),不依赖列表顺序,便于插入新 migration。
# - 不支持 down/回滚:SQLite ALTER TABLE DROP COLUMN 支持有限,且本项目的 migration
#   都是纯增量(加表/加列/加索引),不需要回滚。
# - 新增 migration 时:append 到 MIGRATIONS 列表,version 取 max+1。
#
# 替代的旧实现:SCHEMA 字符串 + _INDEX_PATCHES 列表 + init_db 内散落的 executescript/
# ALTER/try-except,随着 schema 演进 init_db 越来越长且无版本可追溯。

# v1: 基础 5 张表(runs/run_events/approvals/teams/library_agents)+ 关键索引
_MIGRATION_V1 = """
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
CREATE INDEX IF NOT EXISTS idx_run_events_run_id_id ON run_events(run_id, id);

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

CREATE INDEX IF NOT EXISTS idx_approvals_run_id ON approvals(run_id);
CREATE INDEX IF NOT EXISTS idx_approvals_run_id_status ON approvals(run_id, status);
CREATE INDEX IF NOT EXISTS idx_approvals_run_id_requested_at ON approvals(run_id, requested_at);

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

# v2: SP7b evolution_history 表(原在 init_db 内单独 executescript)
_MIGRATION_V2 = """
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
"""

# v3: library_agents 加 version 列(原在 init_db 内 ALTER + try/except)
# SQLite 不支持 ADD COLUMN IF NOT EXISTS,需要 Python 层 try/except
_MIGRATION_V3_ALTER = (
    "ALTER TABLE library_agents ADD COLUMN version INTEGER NOT NULL DEFAULT 1"
)

# v4: 性能索引(原 _INDEX_PATCHES 列表)
# 走 idx_runs_status_team_created 复合索引,dashboard 聚合 + list_runs ORDER BY 都能命中
_MIGRATION_V4 = """
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_team_name ON runs(team_name);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at);
CREATE INDEX IF NOT EXISTS idx_runs_status_team_created ON runs(status, team_name, created_at);
CREATE INDEX IF NOT EXISTS idx_evo_agent_success_ts ON evolution_history(agent_name, success, timestamp, id);
CREATE INDEX IF NOT EXISTS idx_approvals_run_id_status_2 ON approvals(run_id, status);
"""

# v6 ALTER: runs 加 triggered_by_user 列 + run_events 加 trace_id/parent_span_id/chain 列
# (原 v6 中 users/roles/permissions 成品表已移除,这里仅保留 runs/run_events 的 ALTER)
# SQLite 不支持 ADD COLUMN IF NOT EXISTS,需 try/except
_MIGRATION_V6_ALTER_RUNS = (
    "ALTER TABLE runs ADD COLUMN triggered_by_user TEXT"
)
_MIGRATION_V6_ALTER_RUN_EVENTS = (
    "ALTER TABLE run_events ADD COLUMN trace_id TEXT"
)
_MIGRATION_V6_ALTER_RUN_EVENTS_SPAN = (
    "ALTER TABLE run_events ADD COLUMN parent_span_id TEXT"
)
_MIGRATION_V6_ALTER_RUN_EVENTS_CHAIN = (
    "ALTER TABLE run_events ADD COLUMN chain TEXT"
)


def _migration_v6_alters(conn: sqlite3.Connection) -> None:
    """v6 ALTER 兼容旧库:列已存在时静默跳过。"""
    for alter_sql in (
        _MIGRATION_V6_ALTER_RUNS,
        _MIGRATION_V6_ALTER_RUN_EVENTS,
        _MIGRATION_V6_ALTER_RUN_EVENTS_SPAN,
        _MIGRATION_V6_ALTER_RUN_EVENTS_CHAIN,
    ):
        try:
            conn.execute(alter_sql)
        except sqlite3.OperationalError:
            pass


# v8: run_events 加 state_bucket 列(Graph Engineering 学习点 P5 状态四分)
# state_bucket 区分事件所属状态桶:
# - schedule: 调度状态(current_step/completed_steps/skipped_steps 推进)
# - artifact: 工件状态(worker_outputs/final_answer 产物)
# - context:  上下文状态(messages/react_messages 内容)
# - policy:   治理状态(pending_approval/quota_status/pep_decision)
# 与 chain(调用链/工具链/决策链)正交:chain 描述事件归属的链类型,
# state_bucket 描述事件影响的状态域,便于按域独立 checkpoint/审计/统计。
_MIGRATION_V8_ALTER_RUN_EVENTS_BUCKET = (
    "ALTER TABLE run_events ADD COLUMN state_bucket TEXT"
)


def _migration_v8_alters(conn: sqlite3.Connection) -> None:
    """v8 ALTER 兼容旧库:列已存在时静默跳过。"""
    try:
        conn.execute(_MIGRATION_V8_ALTER_RUN_EVENTS_BUCKET)
    except sqlite3.OperationalError:
        pass


# 迁移列表:(version, description, sql_or_callable)
# - sql 为 str:直接 executescript
# - sql 为 callable:调用以 conn 为参数,自行处理(用于 ALTER 等 idempotent 不可表达的场景)
def _migration_v3(conn: sqlite3.Connection) -> None:
    """v3 ALTER 兼容旧库:列已存在时静默跳过(SQLite 无 ADD COLUMN IF NOT EXISTS)。"""
    try:
        conn.execute(_MIGRATION_V3_ALTER)
    except sqlite3.OperationalError:
        # 列已存在(从 v1 创建的旧库或重复迁移);SQLite 此处只会因列已存在抛 OperationalError
        pass


# 跨 run 记忆层:run 终态后由 MemoryEngine 蒸馏的团队级经验条目
_MIGRATION_V12 = """
CREATE TABLE IF NOT EXISTS run_memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    team_name  TEXT NOT NULL,
    run_id     TEXT NOT NULL,
    outcome    TEXT NOT NULL,
    summary    TEXT NOT NULL,
    meta       TEXT,
    tokens     INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_run_memories_team ON run_memories(team_name, id);
"""

# 审批预览:approvals 表补工具名与参数摘要(决策时由 gate 落入,
# 使历史 Approvals 列表可展示「动了什么」)
def _migration_v13_alters(conn: sqlite3.Connection) -> None:
    for column in ("tool_name", "args_preview"):
        try:
            conn.execute(f"ALTER TABLE approvals ADD COLUMN {column} TEXT")
        except sqlite3.OperationalError:
            pass  # 幂等:列已存在(旧库重跑迁移)


# SP12b: IM 通知 outbox(先存后发/退避重试/同 run 串行/回执可查)。
# 借鉴 dsh-superagent Outbox 语义:消息先落库再发送,进程重启后 pending 行
# 自动续发;status: pending → sent / dropped(连续失败达上限,保留 last_error)。
# next_retry_at/created_at/sent_at 用 REAL(unix 秒),比较与退避计算都在 SQL 外做。
_MIGRATION_V14 = """
CREATE TABLE IF NOT EXISTS notify_outbox (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL,
    event         TEXT NOT NULL,
    url           TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    next_retry_at REAL NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL,
    sent_at       REAL
);
CREATE INDEX IF NOT EXISTS idx_outbox_due ON notify_outbox(status, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_outbox_run ON notify_outbox(run_id, id);
"""

# SP12c: 事件驱动 Trigger 规则表(对标 DeepSeek Super-agent 的 Trigger 两级过滤)。
# 规则的可变结构(conditions/task/slow_path/enabled)整体存 rules JSON;
# name 唯一,secret 明文存储(与 auth_api_keys 同级别的本地信任域)但 API 返回时打码。
_MIGRATION_V15 = """
CREATE TABLE IF NOT EXISTS trigger_rules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    team_name  TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'webhook',
    secret     TEXT,
    rules      TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_trigger_rules_team ON trigger_rules(team_name, id);
"""


# SP12 Mandate 授权模型:授权与执行分离。
# runs.mandate_json:run 的授权凭据(basis/submitted_by/policy_version/scope),
#   创建时由 submit_run 按触发身份生成,向下只读继承不放大范围;
# approvals.mandate_version:决策时的授权规则版本(审批门 = 授权管理:
#   通过 = 该操作授权延续,拒绝 = 该 step 授权终止)。
# 两列都 TEXT 可空:旧 run/旧审批行无授权凭据,读取侧按 None 兼容。
def _migration_v16_alters(conn: sqlite3.Connection) -> None:
    for column in ("runs.mandate_json", "approvals.mandate_version"):
        table, col = column.split(".")
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass  # 幂等:列已存在(旧库重跑迁移)


# opencode harness 引擎移除:删除其编排快照表(V11 引入,已无读方)
_MIGRATION_V17 = "DROP TABLE IF EXISTS run_engine_state;"


MIGRATIONS: list[tuple[int, str, object]] = [
    (1, "base tables (runs/run_events/approvals/teams/library_agents)", _MIGRATION_V1),
    (2, "evolution_history table (SP7b)", _MIGRATION_V2),
    (3, "library_agents.version column", _migration_v3),
    (4, "performance indexes (WAL/aggregate)", _MIGRATION_V4),
    (8, "ALTER runs/run_events for v6 columns (triggered_by_user/trace_id/parent_span_id/chain)", _migration_v6_alters),
    (10, "ALTER run_events.state_bucket for v8 (Graph Engineering P5)", _migration_v8_alters),
    (12, "run_memories table (跨 run 记忆层)", _MIGRATION_V12),
    (13, "ALTER approvals for v12 columns (tool_name/args_preview, 审批 diff 预览)", _migration_v13_alters),
    (14, "notify_outbox table (SP12b IM 通知 outbox:先存后发/退避重试/回执)", _MIGRATION_V14),
    (15, "trigger_rules table (SP12c 事件驱动 Trigger:Fast/Slow Path 两级过滤)", _MIGRATION_V15),
    (16, "ALTER runs/approvals for v15 columns (mandate_json/mandate_version, SP12 Mandate 授权)", _migration_v16_alters),
    (17, "drop run_engine_state (opencode harness 移除)", _MIGRATION_V17),
]


def _apply_migration(conn: sqlite3.Connection, sql_or_callable) -> None:
    """执行单个 migration:字符串走 executescript,callable 直接调用。"""
    if callable(sql_or_callable):
        sql_or_callable(conn)
    else:
        conn.executescript(sql_or_callable)


def _get_user_version(conn: sqlite3.Connection) -> int:
    """读取 SQLite 原生 schema 版本号(PRAGMA user_version)。0 表示未初始化。"""
    cur = conn.execute("PRAGMA user_version")
    row = cur.fetchone()
    return int(row[0]) if row else 0


def _set_user_version(conn: sqlite3.Connection, version: int) -> None:
    """更新 schema 版本号。注意:PRAGMA user_version = N 不支持参数绑定,需用 f-string
    (version 是 int,无 SQL 注入风险)。"""
    conn.execute(f"PRAGMA user_version = {int(version)}")


def run_migrations(conn: sqlite3.Connection) -> int:
    """运行所有 version > current_user_version 的 migration,更新 user_version。

    返回:本次实际执行的 migration 数(供运维观测/日志)。

    幂等性:重复调用只执行未应用的 migration;若全部已应用则返回 0。
    兼容性:迁移框架引入前的旧库 user_version=0,所有 migration 会重跑一遍,
    每个 migration 都设计为幂等(IF NOT EXISTS / try-except),重跑无副作用。
    """
    current = _get_user_version(conn)
    applied = 0
    for version, description, sql in sorted(MIGRATIONS, key=lambda m: m[0]):
        if version <= current:
            continue
        _apply_migration(conn, sql)
        _set_user_version(conn, version)
        conn.commit()
        applied += 1
    return applied


def init_db(path: str | Path = "data/agentteam.db") -> sqlite3.Connection:
    """初始化 SQLite 数据库,运行所有未应用的 migration,返回连接。

    启用 WAL 模式:显著降低写者-读者阻塞,允许多个读连接并发,
    单写者仍然由调用方 threading.Lock 保证(app 层 conn_lock)。
    外键约束开启:此前 schema 已有 FOREIGN KEY 声明但未生效。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: the connection may be shared with SqliteSaver,
    # which writes checkpoints from worker threads and serializes access via
    # its own lock. Safe for single-threaded use as well.
    conn = sqlite3.connect(str(p), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # 启用 WAL:写不阻塞读,commit 不再是全页 fsync,大幅降低锁竞争
    conn.execute("PRAGMA journal_mode=WAL")
    # NORMAL 牺牲一点崩溃耐久度换更高吞吐(WAL+NORMAL 是 SQLite 推荐的高吞吐组合)
    conn.execute("PRAGMA synchronous=NORMAL")
    # 外键约束开启(此前 schema 已有 FOREIGN KEY 声明但未生效)
    conn.execute("PRAGMA foreign_keys=ON")
    # 运行所有未应用的 migration(内部已 commit)
    run_migrations(conn)
    return conn
