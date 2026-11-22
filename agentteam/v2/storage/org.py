"""组织架构树仓库(P6: 任意深度,parent_id 自引用;工人挂载在节点上)。

设计要点:
- 层级深度无上限:节点 = (id, parent_id, name, kind, position);parent_id 自引用
- move 做环检测(不能把自己挂到自己子孙下);删除要求无子节点且无挂载工人
- tree() 一次拉全量在内存组嵌套(节点量级=组织数,百级以内,无需递归 SQL)
"""
from __future__ import annotations

import threading
from uuid import uuid4

from agentteam.logging_config import get_logger
from agentteam.storage.utils import utcnow_iso as _now
from agentteam.v2.storage.base import BaseSqliteRepo

logger = get_logger("v2.storage.org")


class OrgRepo(BaseSqliteRepo):
    """组织节点 CRUD + 树组装。"""

    def create(self, name: str, parent_id: str | None = None, *,
               node_id: str | None = None, kind: str = "group",
               position: int = 0) -> dict:
        """创建节点;node_id 显式传入用于种子幂等。根节点 parent_id=None。"""
        nid = node_id or f"org_{uuid4().hex[:10]}"
        now = _now()
        self._execute(
            "INSERT INTO org_nodes (id, parent_id, name, kind, position, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (nid, parent_id, name, kind, position, now, now),
        )
        return self.get(nid)  # type: ignore[return-value]

    def get(self, node_id: str) -> dict | None:
        row = self._fetchone("SELECT * FROM org_nodes WHERE id = ?", (node_id,))
        return dict(row) if row else None

    def list_all(self) -> list[dict]:
        rows = self._fetchall(
            "SELECT * FROM org_nodes ORDER BY position, created_at"
        )
        return [dict(r) for r in rows]

    def update(self, node_id: str, *, name: str | None = None,
               kind: str | None = None, parent_id: str | None = ...,
               position: int | None = None) -> dict | None:
        """更新节点;parent_id 传新父节点 = 移动(带环检测)。省略字段用 ... 哨兵区分。"""
        node = self.get(node_id)
        if node is None:
            return None
        sets: list[str] = ["updated_at = ?"]
        params: list = [_now()]
        if name is not None:
            sets.append("name = ?")
            params.append(name)
        if kind is not None:
            sets.append("kind = ?")
            params.append(kind)
        if position is not None:
            sets.append("position = ?")
            params.append(position)
        if parent_id is not ...:
            if parent_id is not None:
                if parent_id == node_id:
                    raise ValueError("不能把节点移动到自己之下(自身)")
                if self._is_descendant(node_id, parent_id):
                    raise ValueError("不能把节点移动到自己的子孙节点下(会成环)")
            sets.append("parent_id = ?")
            params.append(parent_id)
        params.append(node_id)
        self._execute(f"UPDATE org_nodes SET {', '.join(sets)} WHERE id = ?", tuple(params))
        return self.get(node_id)

    def delete(self, node_id: str, *, has_children: bool, has_workers: bool) -> bool:
        """删除节点(调用方先检查子节点与挂载工人;两层约束,防误删子树)。"""
        if has_children or has_workers:
            raise ValueError("节点下仍有子节点或挂载工人,禁止删除")
        return self._execute(
            "DELETE FROM org_nodes WHERE id = ?", (node_id,)
        ).rowcount > 0

    def _is_descendant(self, ancestor_id: str, candidate_id: str) -> bool:
        """candidate 是否在 ancestor 的子树内(沿 parent 链向上走,深度即层级,无上限)。"""
        seen: set[str] = set()
        current = candidate_id
        while current and current not in seen:
            seen.add(current)
            if current == ancestor_id:
                return True
            row = self._fetchone("SELECT parent_id FROM org_nodes WHERE id = ?", (current,))
            current = row["parent_id"] if row else None
        return False

    def children_count_map(self) -> dict[str, int]:
        rows = self._fetchall(
            "SELECT parent_id, COUNT(*) AS n FROM org_nodes "
            "WHERE parent_id IS NOT NULL GROUP BY parent_id"
        )
        return {r["parent_id"]: r["n"] for r in rows}

    def tree(self) -> list[dict]:
        """全量组嵌套(任意深度);节点附 children 列表。返回根节点列表。"""
        nodes = self.list_all()
        known = {n["id"] for n in nodes}
        by_parent: dict[str | None, list[dict]] = {}
        for n in nodes:
            pid = n["parent_id"] if n["parent_id"] in known else None  # 孤儿防御式挂根
            by_parent.setdefault(pid, []).append({**n, "children": []})
        roots = by_parent.get(None, [])

        def attach(parent_list: list[dict]) -> None:
            for p in parent_list:
                p["children"] = by_parent.get(p["id"], [])
                attach(p["children"])

        attach(roots)
        return roots
