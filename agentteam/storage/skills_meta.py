"""skills / skill_acls 表的读写(P-B5 对标阿里云 AgentTeams "Skill 供应链安全")。

设计要点:
- skills 表:skill 元数据(版本/可见性/状态/所有者)
  - visibility: 'public'(全员可用) / 'private'(仅 owner_team) / 'protected'(需 ACL 授权)
  - status: 'draft' / 'published' / 'deprecated' / 'revoked'
  - revocation 是关键安全能力:发现恶意 skill 后立即 revoke,运行时拒绝加载
- skill_acls:per-consumer 调用授权(team_name → skill_name)
  - protected skill 必须有 ACL 才能被其他 team 加载
  - public skill 不需要 ACL(全员可用)
  - private skill 只能被 owner_team 加载(无需 ACL)

调用集成:
- SkillLoader.load 前,先调 SkillMetaRepo.check_access(skill_name, team_name) 校验
- 校验失败抛 SkillAccessDeniedError,编译期 fail-fast
- revocation 检查:status != 'published' 时拒绝加载(即使 ACL 允许)
"""
from __future__ import annotations

from typing import Any

from agentteam.storage.base import BaseSqliteRepo
from agentteam.storage.utils import utcnow_iso as _now


class SkillAccessDeniedError(Exception):
    """Skill 访问被拒(无 ACL / skill 未发布 / skill 已撤销)。"""

    def __init__(self, skill_name: str, team_name: str, reason: str) -> None:
        self.skill_name = skill_name
        self.team_name = team_name
        self.reason = reason
        super().__init__(
            f"Skill access denied: skill='{skill_name}' team='{team_name}' reason={reason}"
        )


class SkillMetaRepo(BaseSqliteRepo):
    """skills / skill_acls 表的读写 + 访问校验。"""

    def upsert_skill(
        self,
        name: str,
        version: int = 1,
        status: str = "published",
        visibility: str = "public",
        owner_team: str | None = None,
        description: str = "",
    ) -> int:
        """创建或更新 skill 元数据。

        status: 'draft' / 'published' / 'deprecated' / 'revoked'
        visibility: 'public' / 'private' / 'protected'
        """
        if status not in ("draft", "published", "deprecated", "revoked"):
            raise ValueError(f"Invalid status: {status!r}")
        if visibility not in ("public", "private", "protected"):
            raise ValueError(f"Invalid visibility: {visibility!r}")
        now = _now()
        cur = self._execute(
            "INSERT INTO skills (name, version, status, visibility, owner_team, description, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "version=excluded.version, status=excluded.status, "
            "visibility=excluded.visibility, owner_team=excluded.owner_team, "
            "description=excluded.description, updated_at=excluded.updated_at",
            (name, version, status, visibility, owner_team, description, now, now),
        )
        return int(cur.lastrowid) if cur.lastrowid is not None else 0

    def get_skill(self, name: str) -> dict | None:
        row = self._fetchone("SELECT * FROM skills WHERE name = ?", (name,))
        return dict(row) if row else None

    def list_skills(
        self,
        status: str | None = None,
        visibility: str | None = None,
        owner_team: str | None = None,
    ) -> list[dict]:
        """按条件列出 skill。"""
        sql = "SELECT * FROM skills"
        conditions: list[str] = []
        params: list[Any] = []
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        if visibility is not None:
            conditions.append("visibility = ?")
            params.append(visibility)
        if owner_team is not None:
            conditions.append("owner_team = ?")
            params.append(owner_team)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY name"
        rows = self._fetchall(sql, tuple(params))
        return [dict(r) for r in rows]

    def delete_skill(self, name: str) -> bool:
        """删除 skill 元数据(连同 ACL)。

        注意:仅删除元数据,不删除文件系统上的 .md 文件(由 SkillLoader 管理)。
        """
        # 先删 ACL(外键无强约束,但语义上 ACL 应随 skill 一起删除)
        self._execute("DELETE FROM skill_acls WHERE skill_name = ?", (name,))
        cur = self._execute("DELETE FROM skills WHERE name = ?", (name,))
        return cur.rowcount > 0

    def revoke_skill(self, name: str, reason: str = "") -> bool:
        """紧急撤销 skill(发现恶意内容后立即停用)。

        撤销后所有 team 都无法加载该 skill,即使 ACL 已授权。
        """
        cur = self._execute(
            "UPDATE skills SET status = 'revoked', updated_at = ? WHERE name = ?",
            (_now(), name),
        )
        return cur.rowcount > 0

    # ---- ACL 管理 ----

    def grant_access(self, skill_name: str, team_name: str) -> None:
        """授予 team 对 skill 的访问权限(用于 protected skill)。"""
        self._execute(
            "INSERT INTO skill_acls (skill_name, team_name, granted_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(skill_name, team_name) DO NOTHING",
            (skill_name, team_name, _now()),
        )

    def revoke_access(self, skill_name: str, team_name: str) -> bool:
        cur = self._execute(
            "DELETE FROM skill_acls WHERE skill_name = ? AND team_name = ?",
            (skill_name, team_name),
        )
        return cur.rowcount > 0

    def list_acls(self, skill_name: str | None = None) -> list[dict]:
        """列出 ACL,可按 skill_name 过滤。"""
        if skill_name is not None:
            rows = self._fetchall(
                "SELECT * FROM skill_acls WHERE skill_name = ? ORDER BY team_name",
                (skill_name,),
            )
        else:
            rows = self._fetchall(
                "SELECT * FROM skill_acls ORDER BY skill_name, team_name"
            )
        return [dict(r) for r in rows]

    # ---- 访问校验 ----

    def check_access(
        self, skill_name: str, team_name: str | None
    ) -> tuple[bool, str]:
        """检查 team 是否可加载 skill,返回 (allowed, reason)。

        校验规则:
        1. skill 元数据不存在 → 拒绝(防止加载未注册的 shadow skill)
        2. status != 'published' → 拒绝(draft/deprecated/revoked 都不可用)
        3. visibility = 'public' → 允许(全员可用)
        4. visibility = 'private' → 仅 owner_team == team_name 时允许
        5. visibility = 'protected' → team_name 在 skill_acls 中时允许
        6. 其他情况 → 拒绝

        team_name=None 时,视为匿名调用,仅 public skill 允许。
        """
        skill = self.get_skill(skill_name)
        if skill is None:
            return False, f"skill '{skill_name}' not registered"
        if skill["status"] != "published":
            return False, f"skill '{skill_name}' status is '{skill['status']}' (not published)"
        visibility = skill["visibility"]
        if visibility == "public":
            return True, ""
        if team_name is None:
            return False, f"skill '{skill_name}' is {visibility}, requires authenticated team"
        if visibility == "private":
            if skill["owner_team"] == team_name:
                return True, ""
            return False, (
                f"skill '{skill_name}' is private to team '{skill['owner_team']}'"
            )
        if visibility == "protected":
            row = self._fetchone(
                "SELECT 1 FROM skill_acls WHERE skill_name = ? AND team_name = ?",
                (skill_name, team_name),
            )
            if row is None:
                return False, (
                    f"skill '{skill_name}' is protected, no ACL for team '{team_name}'"
                )
            return True, ""
        return False, f"skill '{skill_name}' has unknown visibility '{visibility}'"


def check_skill_access(
    skill_meta_repo: SkillMetaRepo | None,
    skill_name: str,
    team_name: str | None,
) -> None:
    """Skill 加载前的访问校验入口。

    skill_meta_repo=None 时直接放行(未启用 Skill 供应链,向后兼容)。
    校验失败抛 SkillAccessDeniedError,由 SkillLoader 编译期捕获并 fail-fast。
    """
    if skill_meta_repo is None:
        return
    allowed, reason = skill_meta_repo.check_access(skill_name, team_name)
    if not allowed:
        raise SkillAccessDeniedError(skill_name, team_name or "", reason)
