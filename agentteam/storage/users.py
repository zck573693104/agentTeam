"""users / roles / permissions / user_roles 表的读写(P-B1 对标阿里云 AgentTeams "访问控制")。

设计要点:
- API Key 不再明文存储,而是存 sha256 hash;校验时 hash 后比较
- 角色体系 4 种:admin(平台管理员 L1)/ manager(团队管理员 L2 L3)/ team_admin(团队 Owner)
  / user(普通用户),team_admin 仅对 user_roles.team_name 指定的 team 生效
- permissions 是 role_name × action 的多对多关系;action 形如 "team:create"/"run:approve"/
  "quota:set" 等
- 默认角色权限矩阵在 ensure_default_roles 中初始化

WAT 双身份:用户通过 X-API-Key 认证后,中间件把 user 注入 request.state.user,
后续路由可读取 user.username 作为 actor 写入 audit_events,Run 创建时也写入
runs.triggered_by_user(P-B2)。
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any

from agentteam.storage.base import BaseSqliteRepo
from agentteam.storage.utils import utcnow_iso as _now


def _hash_api_key(api_key: str) -> str:
    """sha256 hash,用于存储与比较 API Key(不存明文)。"""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


# 默认角色 + 权限矩阵
# 对标阿里云 AgentTeams L1 Admin / L2 Team Leader / L3 Worker + TeamAdmin 业务 Owner
_DEFAULT_ROLES: dict[str, str] = {
    "admin":      "平台管理员,所有权限(L1)",
    "manager":    "运维管理员,Team/Quota/Skill 管理",
    "team_admin": "团队 Owner,仅对自己负责的 team 有管理权限",
    "user":       "普通用户,只能启动 run 与查询",
}

# action 命名约定:<resource>:<verb>
# resource: team / library_agent / run / quota / skill / mcp / user / audit / pep
_DEFAULT_PERMISSIONS: dict[str, list[str]] = {
    "admin": [
        "*",  # 通配,所有 action
    ],
    "manager": [
        "team:list", "team:create", "team:update", "team:delete",
        "library_agent:list", "library_agent:create", "library_agent:update", "library_agent:delete",
        "quota:get", "quota:set", "quota:delete",
        "skill:list", "skill:publish", "skill:review",
        "mcp:configure",
        "audit:list",
        "pep:manage",
        "user:list",
    ],
    "team_admin": [
        "team:list", "team:get",
        "library_agent:list",
        "run:create", "run:list", "run:get", "run:approve", "run:cancel",
        "quota:get",
        "skill:list",
        "audit:list",
    ],
    "user": [
        "team:list", "team:get",
        "library_agent:list",
        "run:create", "run:list", "run:get",
        "skill:list",
    ],
}


class UserRepo(BaseSqliteRepo):
    """users / roles / permissions / user_roles 表的读写。"""

    def ensure_default_roles(self) -> None:
        """初始化默认角色与权限矩阵(幂等,重复调用无副作用)。

        在 init_db 后或 create_app 启动时调用,确保 4 种角色与默认权限存在。
        """
        for name, desc in _DEFAULT_ROLES.items():
            self._execute(
                "INSERT INTO roles (name, description) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET description=excluded.description",
                (name, desc),
            )
        for role, actions in _DEFAULT_PERMISSIONS.items():
            for action in actions:
                self._execute(
                    "INSERT INTO permissions (role_name, action) VALUES (?, ?) "
                    "ON CONFLICT(role_name, action) DO NOTHING",
                    (role, action),
                )

    def create_user(
        self,
        username: str,
        api_key: str,
        display_name: str = "",
        email: str | None = None,
    ) -> dict[str, Any]:
        """创建用户,返回 {id, username, api_key}(api_key 仅此一次返回,后续不再可见)。"""
        user_id = uuid.uuid4().hex
        now = _now()
        api_key_hash = _hash_api_key(api_key)
        self._execute(
            "INSERT INTO users (id, username, display_name, email, api_key_hash, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'active', ?, ?)",
            (user_id, username, display_name, email, api_key_hash, now, now),
        )
        return {"id": user_id, "username": username, "api_key": api_key}

    def get_by_api_key(self, api_key: str) -> dict[str, Any] | None:
        """根据 API Key 查找用户(用于鉴权中间件)。

        返回 dict 含 id/username/display_name/email/status/roles:
        - roles: [(role_name, team_name)] 列表,team_name 为 None 表示全局角色
        """
        api_key_hash = _hash_api_key(api_key)
        row = self._fetchone(
            "SELECT id, username, display_name, email, status FROM users "
            "WHERE api_key_hash = ? AND status = 'active'",
            (api_key_hash,),
        )
        if row is None:
            return None
        roles = self._fetchall(
            "SELECT role_name, team_name FROM user_roles WHERE user_id = ?",
            (row["id"],),
        )
        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "email": row["email"],
            "status": row["status"],
            "roles": [(r["role_name"], r["team_name"]) for r in roles],
        }

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        row = self._fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
        return dict(row) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        rows = self._fetchall("SELECT id, username, display_name, email, status FROM users ORDER BY username")
        return [dict(r) for r in rows]

    def assign_role(
        self, user_id: str, role_name: str, team_name: str | None = None
    ) -> None:
        """给用户分配角色(team_admin 必须传 team_name)。"""
        self._execute(
            "INSERT INTO user_roles (user_id, role_name, team_name) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, role_name, team_name) DO NOTHING",
            (user_id, role_name, team_name),
        )

    def revoke_role(
        self, user_id: str, role_name: str, team_name: str | None = None
    ) -> bool:
        cur = self._execute(
            "DELETE FROM user_roles WHERE user_id = ? AND role_name = ? AND team_name IS ?",
            (user_id, role_name, team_name),
        )
        return cur.rowcount > 0

    def list_roles(self) -> list[dict[str, Any]]:
        rows = self._fetchall("SELECT * FROM roles ORDER BY name")
        return [dict(r) for r in rows]

    def list_permissions(self, role_name: str) -> list[str]:
        rows = self._fetchall(
            "SELECT action FROM permissions WHERE role_name = ?", (role_name,)
        )
        return [r["action"] for r in rows]

    def check_permission(
        self, user_id: str, action: str, team_name: str | None = None
    ) -> bool:
        """检查用户是否有某 action 的权限。

        优先级:
        1. 用户的全局角色(admin/manager)有该 action 或通配 "*"
        2. 若 team_name 非空,team_admin 角色在 team_name 上下文下有该 action
        3. 普通用户 user 角色有该 action
        """
        # 1. 全局角色 + team-scoped 角色一并查询
        sql = (
            "SELECT p.action, ur.team_name FROM user_roles ur "
            "JOIN permissions p ON p.role_name = ur.role_name "
            "WHERE ur.user_id = ?"
        )
        params: list[Any] = [user_id]
        if team_name is not None:
            sql += " AND (ur.team_name IS NULL OR ur.team_name = ?)"
            params.append(team_name)
        rows = self._fetchall(sql, tuple(params))
        for r in rows:
            # 通配 "*" 视为有所有权限(仅 admin 角色)
            if r["action"] == "*" or r["action"] == action:
                return True
        return False

    def delete_user(self, user_id: str) -> bool:
        """软删除:status='deleted',保留审计记录。"""
        cur = self._execute(
            "UPDATE users SET status = 'deleted', updated_at = ? WHERE id = ?",
            (_now(), user_id),
        )
        return cur.rowcount > 0
