"""Storage 层共享基类:封装 conn/lock 与 SQL 执行模板。

抽 BaseSqliteRepo(P2-1 优化):
原 5 个 Repo(RunRepo/AuditRepo/TeamRepo/LibraryRepo/EvolutionRepo)的 __init__
完全一致(`self._conn = conn; self._lock = lock or threading.Lock()`),且 31 处
`with self._lock: cur = self._conn.execute(...); self._conn.commit()` 模板代码重复。
每次新增 Repo 都要复制一遍锁样板,且改锁逻辑需改 5 处。

基类提供三个 helper:
- _execute(sql, params):带锁执行写操作(INSERT/UPDATE/DELETE),自动 commit,返回 cursor
- _fetchone(sql, params):带锁查询单行,返回 Row | None
- _fetchall(sql, params):带锁查询多行,返回 list[Row]

子类只写 SQL 与反序列化,锁逻辑统一在基类维护。
"""
from __future__ import annotations

import sqlite3
import threading


class BaseSqliteRepo:
    """SQLite Repo 基类:封装 conn/lock 与 SQL 执行模板。

    子类继承后只需用 self._execute/_fetchone/_fetchall 写 SQL,
    无需重复 with self._lock 模板。

    当与 SqliteSaver / 其他 Repo 共享同一 sqlite3.Connection 时,
    须传入同一个 lock 以串行化所有连接访问(sqlite3.Connection 多线程非线程安全,
    即使 check_same_thread=False)。
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock | None = None) -> None:
        self._conn = conn
        self._lock = lock or threading.Lock()

    def _execute(
        self, sql: str, params: tuple = ()
    ) -> sqlite3.Cursor:
        """带锁执行写操作(INSERT/UPDATE/DELETE),自动 commit,返回 cursor。

        cursor.rowcount / cursor.lastrowid 可由调用方读取。
        """
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _fetchone(
        self, sql: str, params: tuple = ()
    ) -> sqlite3.Row | None:
        """带锁查询单行,返回 Row | None。"""
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.fetchone()

    def _fetchall(
        self, sql: str, params: tuple = ()
    ) -> list[sqlite3.Row]:
        """带锁查询多行,返回 list[Row]。"""
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.fetchall()
