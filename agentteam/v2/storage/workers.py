"""Worker Pool 仓库(CRUD)。

缓存一致性说明:查询走 base._fetchall(自带锁),缓存字典用独立的
cache_lock 保护——禁止在持有 repo 锁时再调 base 方法(v1 教训:
threading.Lock 不可重入,嵌套 = 自死锁)。
"""
from __future__ import annotations

import json
import threading

from agentteam.storage.utils import utcnow_iso as _now
from agentteam.v2.domain.worker import WorkerDef, worker_from_row
from agentteam.v2.storage.base import BaseSqliteRepo


class WorkerRepo(BaseSqliteRepo):
    def __init__(self, conn, lock: threading.Lock | None = None) -> None:
        super().__init__(conn, lock)
        self._cache: dict[str, WorkerDef] = {}
        self._cache_lock = threading.Lock()
        self._cache_loaded = False

    def _load_cache(self) -> None:
        """全量加载到内存缓存(幂等;竞态时后写覆盖,数据相同无害)。"""
        if self._cache_loaded:
            return
        rows = self._fetchall("SELECT * FROM workers")
        loaded = {r["name"]: worker_from_row(r) for r in rows}
        with self._cache_lock:
            if not self._cache_loaded:
                self._cache = loaded
                self._cache_loaded = True

    def upsert(self, w: WorkerDef) -> None:
        now = _now()
        self._execute(
            "INSERT INTO workers (name, system_prompt, model_json, tools_json, skills_json, "
            "approval_json, max_iterations, enabled, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET system_prompt=excluded.system_prompt, "
            "model_json=excluded.model_json, tools_json=excluded.tools_json, "
            "skills_json=excluded.skills_json, approval_json=excluded.approval_json, "
            "max_iterations=excluded.max_iterations, enabled=excluded.enabled, "
            "updated_at=excluded.updated_at",
            (
                w.name, w.system_prompt,
                json.dumps(w.model, ensure_ascii=False) if w.model else None,
                json.dumps(w.tools), json.dumps(w.skills), json.dumps(w.approval_targets),
                w.max_iterations, 1 if w.enabled else 0, now, now,
            ),
        )
        with self._cache_lock:
            self._cache[w.name] = w

    def get(self, name: str) -> WorkerDef | None:
        self._load_cache()
        return self._cache.get(name)

    def list_all(self, include_disabled: bool = True) -> list[WorkerDef]:
        self._load_cache()
        workers = list(self._cache.values())
        if not include_disabled:
            workers = [w for w in workers if w.enabled]
        return sorted(workers, key=lambda w: w.name)

    def delete(self, name: str) -> bool:
        cur = self._execute("DELETE FROM workers WHERE name = ?", (name,))
        with self._cache_lock:
            self._cache.pop(name, None)
        return cur.rowcount > 0
