"""IM 通知 outbox 持久化(SP12b)。

借鉴 dsh-superagent Outbox:对外消息「先存后发」——
- enqueue 在 run 事件路径上同步落库(进程重启不丢通知);
- OutboxWorker 后台按 (run_id, id) 序取到期行发送;
- 回执(status/attempts/last_error/时间)落库可查,
  GET /api/runs/{id}/notifications 直接读本表。

与 BaseSqliteRepo 同风格:共享 conn + conn_lock,子类只写 SQL。
"""
from __future__ import annotations

import sqlite3
import threading

from agentteam.storage.base import BaseSqliteRepo


class NotifyOutboxRepo(BaseSqliteRepo):
    """notify_outbox 表的读写(唯一写入方,与 dsh Outbox 同约束)。"""

    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock | None = None) -> None:
        super().__init__(conn, lock)
        # 行以 dict 风格访问(生产 conn 由 init_db 设好 Row 工厂;
        # 测试用裸 conn 时在此兜底,幂等)
        if self._conn.row_factory is None:
            self._conn.row_factory = sqlite3.Row
        self._init_table()

    def _init_table(self) -> None:
        """建表兜底:迁移框架负责 schema,这里对内存库/裸 conn 容错。

        生产路径 init_db 已跑 v14 迁移;测试可能用未迁移的内存 conn
        直接构造本 repo,IF NOT EXISTS 保证幂等。
        """
        with self._lock:
            self._conn.execute(
                """
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
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_outbox_due "
                "ON notify_outbox(status, next_retry_at)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_outbox_run "
                "ON notify_outbox(run_id, id)"
            )
            self._conn.commit()

    # ---------------- 写 ----------------

    def enqueue(self, run_id: str, event: str, url: str, payload_json: str,
                now: float) -> int:
        """落一条待发通知,返回行 id。payload 为最终请求体 JSON。"""
        cur = self._execute(
            """
            INSERT INTO notify_outbox
                (run_id, event, url, payload_json, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (run_id, event, url, payload_json, now),
        )
        return int(cur.lastrowid or 0)

    def mark_sent(self, row_id: int, sent_at: float) -> None:
        self._execute(
            "UPDATE notify_outbox SET status='sent', sent_at=? WHERE id=?",
            (sent_at, row_id),
        )

    def mark_retry(self, row_id: int, attempts: int, next_retry_at: float,
                   last_error: str) -> None:
        """记录一次失败与下次重试时刻(退避由调用方计算)。"""
        self._execute(
            "UPDATE notify_outbox SET attempts=?, next_retry_at=?, last_error=? "
            "WHERE id=?",
            (attempts, next_retry_at, last_error, row_id),
        )

    def mark_dropped(self, row_id: int, last_error: str) -> None:
        """连续失败达上限:置 dropped,保留 last_error 供审计(不再重试)。"""
        self._execute(
            "UPDATE notify_outbox SET status='dropped', last_error=? WHERE id=?",
            (last_error, row_id),
        )

    # ---------------- 读 ----------------

    def pending_rows(self) -> list[sqlite3.Row]:
        """全部 pending 行,按 (run_id, id) 排序——同 run 串行靠它保证。"""
        return self._fetchall(
            "SELECT * FROM notify_outbox WHERE status='pending' "
            "ORDER BY run_id, id"
        )

    def list_for_run(self, run_id: str) -> list[sqlite3.Row]:
        """回执查询:某 run 的全部通知(含 sent/dropped)。"""
        return self._fetchall(
            "SELECT * FROM notify_outbox WHERE run_id=? ORDER BY id",
            (run_id,),
        )
