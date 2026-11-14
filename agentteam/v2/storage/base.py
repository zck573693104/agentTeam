"""SQLite Repo 基类(移植自 v1 storage/base.py,经全量测试验证的模板)。

子类只写 SQL 与反序列化,锁逻辑统一在基类维护。
共享同一 sqlite3.Connection 的组件须传入同一把 lock。
"""
from __future__ import annotations

import sqlite3
import threading


class BaseSqliteRepo:
    """封装 conn/lock 与 SQL 执行模板。

    - _execute: 带锁写操作(INSERT/UPDATE/DELETE),自动 commit
    - _fetchone / _fetchall: 带锁查询
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock | None = None) -> None:
        self._conn = conn
        self._lock = lock or threading.Lock()

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.fetchone()

    def _fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.fetchall()
