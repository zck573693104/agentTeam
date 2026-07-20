"""PEP (Policy Enforcement Point) — 指令级拦截器。

P-B4 对标阿里云 AgentTeams "零信任安全架构":
- 在 Skill/MCP 调用前评估策略,deny 则抛 PEPCDeniedError 阻断调用
- 策略存储在 pep_policies 表,effect=allow/deny
- principal/action/resource/condition 四元组描述策略
- condition 暂用 JSON 表达,后续可扩展为 CEL/Rego 等

策略评估逻辑(类 AWS IAM):
1. 找出 principal 匹配 + action 匹配 + resource 匹配的所有策略
2. 显式 deny 优先(任一 deny 即拒)
3. 无 deny 时,有 allow 则放行
4. 无任何匹配策略时,默认拒绝(零信任)

典型用法(在 make_tool_step 中):
    from agentteam.runtime.pep import check_pep, PEPDeniedError
    try:
        check_pep(pep_repo, principal=agent.name,
                  action="tool:invoke", resource=tc["name"])
    except PEPDeniedError as e:
        # 拒绝调用,写 ToolMessage 返回 LLM
        ...
"""
from __future__ import annotations

import json
from typing import Any

from agentteam.storage.base import BaseSqliteRepo


class PEPDeniedError(Exception):
    """PEP 策略评估拒绝时抛出,中断工具/Skill/MCP 调用。"""

    def __init__(self, principal: str, action: str, resource: str, reason: str = "") -> None:
        self.principal = principal
        self.action = action
        self.resource = resource
        self.reason = reason
        super().__init__(
            f"PEP denied: principal={principal} action={action} "
            f"resource={resource} reason={reason}"
        )


class PEPRepo(BaseSqliteRepo):
    """pep_policies 表的读写 + 评估逻辑。

    pep_policies schema(v7 migration):
        id          INTEGER PRIMARY KEY AUTOINCREMENT
        name        TEXT UNIQUE
        effect      TEXT  -- 'allow' / 'deny'
        principal   TEXT  -- agent 名 / team 名 / '*'(通配)
        action      TEXT  -- 'tool:invoke' / 'skill:load' / 'mcp:call' / '*'
        resource    TEXT  -- 工具名 / skill 名 / MCP server 名 / '*'
        condition   TEXT  -- JSON,后续扩展(目前未评估)
    """

    def upsert_policy(
        self,
        name: str,
        effect: str,
        principal: str,
        action: str,
        resource: str,
        condition: dict | None = None,
    ) -> int:
        """创建或更新策略(以 name 为唯一键)。"""
        if effect not in ("allow", "deny"):
            raise ValueError(f"effect must be 'allow' or 'deny', got {effect!r}")
        from agentteam.storage.utils import utcnow_iso as _now
        now = _now()
        cond_str = json.dumps(condition or {}, ensure_ascii=False)
        cur = self._execute(
            "INSERT INTO pep_policies (name, effect, principal, action, resource, condition, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "effect=excluded.effect, principal=excluded.principal, "
            "action=excluded.action, resource=excluded.resource, "
            "condition=excluded.condition, updated_at=excluded.updated_at",
            (name, effect, principal, action, resource, cond_str, now, now),
        )
        return int(cur.lastrowid) if cur.lastrowid is not None else 0

    def delete_policy(self, name: str) -> bool:
        cur = self._execute("DELETE FROM pep_policies WHERE name = ?", (name,))
        return cur.rowcount > 0

    def list_policies(self) -> list[dict]:
        rows = self._fetchall("SELECT * FROM pep_policies ORDER BY name")
        return [dict(r) for r in rows]

    def _match(self, pattern: str, value: str) -> bool:
        """模式匹配:'*' 通配一切,否则精确匹配(忽略大小写)。"""
        if pattern == "*":
            return True
        return pattern.lower() == value.lower()

    def evaluate(
        self,
        principal: str,
        action: str,
        resource: str,
    ) -> tuple[bool, str]:
        """评估策略,返回 (allowed, reason)。

        评估规则(类 AWS IAM):
        1. 收集所有匹配的策略(principal/action/resource 三元组都匹配)
        2. 任一 deny → 拒绝(显式 deny 优先)
        3. 有 allow 且无 deny → 放行
        4. 无任何匹配 → 默认拒绝(零信任)

        Returns:
            (allowed, reason):allowed=True 时 reason='';allowed=False 时 reason 解释原因
        """
        rows = self._fetchall(
            "SELECT name, effect, principal, action, resource, condition "
            "FROM pep_policies"
        )
        has_allow = False
        deny_reason = ""
        for r in rows:
            if not (
                self._match(r["principal"], principal)
                and self._match(r["action"], action)
                and self._match(r["resource"], resource)
            ):
                continue
            if r["effect"] == "deny":
                return False, f"denied by policy '{r['name']}'"
            if r["effect"] == "allow":
                has_allow = True
        if has_allow:
            return True, ""
        # 无匹配策略:默认拒绝(零信任)
        return False, (
            f"no matching allow policy for principal={principal} "
            f"action={action} resource={resource}"
        )


def check_pep(
    pep_repo: PEPRepo | None,
    principal: str,
    action: str,
    resource: str,
) -> None:
    """PEP 入口:评估策略,拒绝则抛 PEPDeniedError。

    pep_repo=None 时直接放行(未启用 PEP,向后兼容)。
    用于 make_tool_step / SkillLoader / MCP client 等调用点。
    """
    if pep_repo is None:
        return
    allowed, reason = pep_repo.evaluate(principal, action, resource)
    if not allowed:
        raise PEPDeniedError(principal, action, resource, reason)
