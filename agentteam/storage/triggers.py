"""TriggerRepo:事件驱动触发规则的 SQLite 持久化(SP12c)。"""
from __future__ import annotations

import json

from agentteam.domain.trigger import TriggerRule, rule_from_row
from agentteam.storage.base import BaseSqliteRepo


class TriggerRepo(BaseSqliteRepo):
    """trigger_rules 表的 CRUD 仓库。

    线程安全:与其他 Repo 共享同一 sqlite3.Connection,
    必须传入同一把 threading.Lock 串行化所有访问。
    """

    @staticmethod
    def _rules_json(rule: TriggerRule) -> str:
        return json.dumps(
            {
                "conditions": rule.conditions,
                "task_from": rule.task_from,
                "task_template": rule.task_template,
                "slow_path": rule.slow_path,
                "enabled": rule.enabled,
            },
            ensure_ascii=False,
        )

    def add_rule(self, rule: TriggerRule) -> int:
        """插入规则,name 唯一;重复时抛 ValueError(路由转 400)。"""
        try:
            cur = self._execute(
                "INSERT INTO trigger_rules (name, team_name, source, secret, rules) "
                "VALUES (?, ?, ?, ?, ?)",
                (rule.name, rule.team_name, rule.source, rule.secret,
                 self._rules_json(rule)),
            )
        except Exception as e:
            if "UNIQUE" in str(e):
                raise ValueError(f"Trigger rule already exists: {rule.name}")
            raise
        return cur.lastrowid  # type: ignore[return-value]

    def get_by_name(self, name: str) -> TriggerRule | None:
        row = self._fetchone("SELECT * FROM trigger_rules WHERE name = ?", (name,))
        if row is None:
            return None
        return rule_from_row(dict(row))

    def list_rules(self, team_name: str | None = None) -> list[TriggerRule]:
        """列出规则(可按 team 过滤),新→旧。损坏行跳过并保留其他结果。"""
        if team_name is None:
            rows = self._fetchall("SELECT * FROM trigger_rules ORDER BY id DESC")
        else:
            rows = self._fetchall(
                "SELECT * FROM trigger_rules WHERE team_name = ? ORDER BY id DESC",
                (team_name,),
            )
        out = []
        for row in rows:
            try:
                out.append(rule_from_row(dict(row)))
            except ValueError:
                continue
        return out

    def update_rule(self, rule: TriggerRule) -> bool:
        """按 name 整体替换规则内容。返回是否存在并更新。"""
        cur = self._execute(
            "UPDATE trigger_rules SET team_name = ?, source = ?, secret = ?, rules = ? "
            "WHERE name = ?",
            (rule.team_name, rule.source, rule.secret, self._rules_json(rule), rule.name),
        )
        return cur.rowcount > 0

    def delete_rule(self, name: str) -> bool:
        cur = self._execute("DELETE FROM trigger_rules WHERE name = ?", (name,))
        return cur.rowcount > 0
