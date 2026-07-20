"""全局依赖容器:供装饰器与路由访问共享 repo 实例。

为什么需要这个:
- require_permission 装饰器在 auth.py 中定义,需要访问 user_repo 做权限检查
- 但 auth.py 不能直接 import server.py(create_app 依赖循环)
- 用模块级 holder 解耦:server.create_app 启动时调 set_user_repo 注入,
  auth.require_permission 通过 get_user_repo 读取

仅用于装饰器场景;路由仍通过参数注入获取 repo(显式优于隐式)。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentteam.storage.users import UserRepo
    from agentteam.storage.quotas import QuotaRepo
    from agentteam.storage.admin_audit import AdminAuditRepo

_user_repo: "UserRepo | None" = None
_quota_repo: "QuotaRepo | None" = None
_admin_audit_repo: "AdminAuditRepo | None" = None


def set_user_repo(repo: "UserRepo | None") -> None:
    global _user_repo
    _user_repo = repo


def get_user_repo() -> "UserRepo | None":
    return _user_repo


def set_quota_repo(repo: "QuotaRepo | None") -> None:
    global _quota_repo
    _quota_repo = repo


def get_quota_repo() -> "QuotaRepo | None":
    return _quota_repo


def set_admin_audit_repo(repo: "AdminAuditRepo | None") -> None:
    global _admin_audit_repo
    _admin_audit_repo = repo


def get_admin_audit_repo() -> "AdminAuditRepo | None":
    return _admin_audit_repo
