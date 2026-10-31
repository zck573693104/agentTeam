"""MemoryRepo:跨 run 团队记忆的 SQLite 持久化(记忆层)。"""
from __future__ import annotations

import json
from typing import Any

from agentteam.storage.base import BaseSqliteRepo


class MemoryRepo(BaseSqliteRepo):
    """run_memories 表的 CRUD 仓库。

    表 schema(由 migration v12 创建):
        id, team_name, run_id, outcome, summary, meta, tokens, created_at

    一条记忆 = 一次 run 终态后的蒸馏产物,按 team 聚合:
    下次同团队 run 启动时取最近 N 条注入 leader 上下文。

    线程安全:与 RunRepo/AuditRepo 共享同一 sqlite3.Connection,
    必须传入同一把 threading.Lock 串行化所有访问。
    """

    def add_memory(
        self,
        team_name: str,
        run_id: str,
        outcome: str,
        summary: str,
        meta: dict[str, Any] | None = None,
        tokens: int = 0,
    ) -> int:
        """插入一条记忆,返回新 id。

        outcome: 'completed' | 'failed'(与 runs.status 对齐)。
        meta: 结构化补充(files_touched / key_points 等),JSON 序列化存储。
        """
        cur = self._execute(
            """
            INSERT INTO run_memories (team_name, run_id, outcome, summary, meta, tokens)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (team_name, run_id, outcome, summary,
             json.dumps(meta, ensure_ascii=False) if meta else None, tokens),
        )
        return cur.lastrowid  # type: ignore[return-value]

    def list_for_team(self, team_name: str, limit: int = 8) -> list[dict]:
        """按时间倒序返回该团队最近 limit 条记忆(新→旧)。"""
        rows = self._fetchall(
            """
            SELECT * FROM run_memories
            WHERE team_name = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (team_name, limit),
        )
        return [self._row_to_dict(row) for row in rows]

    def delete_memory(self, team_name: str, memory_id: int) -> bool:
        """删除一条记忆(记忆卫生)。校验 team 归属,返回是否存在并删除。"""
        cur = self._execute(
            "DELETE FROM run_memories WHERE id = ? AND team_name = ?",
            (memory_id, team_name),
        )
        return cur.rowcount > 0

    @staticmethod
    def _row_to_dict(row) -> dict:
        """Row → dict,并把 meta JSON 列还原为 dict(损坏时退化为 None)。"""
        d = dict(row)
        if d.get("meta"):
            try:
                d["meta"] = json.loads(d["meta"])
            except (json.JSONDecodeError, TypeError):
                pass
        return d
