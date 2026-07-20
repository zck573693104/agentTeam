"""API Key 鉴权中间件 + RBAC 权限检查(对标阿里云 AgentTeams "访问控制")。

两层鉴权:
1. **身份认证**:校验 X-API-Key,优先查 users 表(P-B1),命中则注入 request.state.user
   未配置 users 表或 auth_api_keys 时退化为原有单一 key 列表模式(向后兼容)
2. **权限校验**:路由通过 require_permission(action, team_name_from=...) 装饰器,
   查 user_roles + permissions 表确认权限

设计:
- 默认关闭(auth_enabled=false),开发态零配置可启动
- 启用后所有 /api/* 请求必须带 X-API-Key header
- GET /api/health 与 / 路径(前端静态)豁免
- 鉴权失败返回 401;权限不足返回 403

环境变量:
- AGENTTEAM_AUTH_ENABLED=true|false
- AGENTTEAM_AUTH_API_KEYS=key1,key2,key3 (legacy,无 RBAC)
"""
from __future__ import annotations

import functools
import secrets
from typing import Awaitable, Callable, Iterable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from agentteam.config import get_settings
from agentteam.logging_config import get_logger

logger = get_logger("api.auth")

# 豁免路径前缀/精确匹配(无需鉴权)
_EXEMPT_PATH_PREFIXES = ("/docs", "/redoc", "/openapi.json", "/static")
_EXEMPT_PATHS_EXACT = {"/", "/api/health"}


def _parse_api_keys(raw: str) -> set[str]:
    """解析逗号分隔的 API key 列表,去掉空白与空串。"""
    return {k.strip() for k in raw.split(",") if k.strip()}


def _is_exempt(path: str) -> bool:
    if path in _EXEMPT_PATHS_EXACT:
        return True
    return any(path.startswith(p) for p in _EXEMPT_PATH_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    """统一鉴权中间件:支持 RBAC(优先)与 legacy 单一 key 列表(回退)。

    - user_repo 非 None 时走 RBAC:校验 X-API-Key → users 表 → 注入 request.state.user
    - 否则走 legacy:校验 X-API-Key 在 auth_api_keys 集合中(无 user 注入)
    """

    def __init__(
        self,
        app,
        valid_keys: Iterable[str] = (),
        user_repo=None,
    ) -> None:
        super().__init__(app)
        self._valid_keys = set(valid_keys)
        self._user_repo = user_repo

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if _is_exempt(path):
            return await call_next(request)
        # 非 /api/* 路径(前端静态资源)直接放行
        if not path.startswith("/api/"):
            return await call_next(request)

        provided = request.headers.get("X-API-Key", "")
        if not provided:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing X-API-Key header"},
            )

        # 1. 优先走 RBAC:users 表查询
        if self._user_repo is not None:
            user = self._user_repo.get_by_api_key(provided)
            if user is None:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or revoked API key"},
                )
            request.state.user = user
            return await call_next(request)

        # 2. legacy 模式:常数时间比较
        ok = False
        for k in self._valid_keys:
            if secrets.compare_digest(provided, k):
                ok = True
                break
        if not ok:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid X-API-Key header"},
            )
        return await call_next(request)


# 保留旧名兼容(测试中可能引用)
ApiKeyMiddleware = AuthMiddleware


def setup_auth(app: FastAPI, user_repo=None) -> bool:
    """根据 Settings 决定是否安装 AuthMiddleware,返回是否启用。

    优先使用 user_repo(RBAC 模式);若未传 user_repo 则回退到 legacy 单一 key 列表。
    """
    settings = get_settings()
    if not settings.auth_enabled:
        return False

    if user_repo is not None:
        app.add_middleware(AuthMiddleware, user_repo=user_repo)
        logger.info("RBAC auth enabled (user_repo provided)")
        return True

    # legacy 模式:用 auth_api_keys 单一列表
    valid_keys = _parse_api_keys(settings.auth_api_keys)
    if not valid_keys:
        valid_keys = {"__never_match__"}
    app.add_middleware(AuthMiddleware, valid_keys=valid_keys)
    logger.info("Legacy API key auth enabled (no user_repo)")
    return True


# ---- 权限检查装饰器(供路由使用) ----

def require_permission(
    action: str,
    team_name_param: str | None = None,
) -> Callable:
    """FastAPI 路由权限检查装饰器。

    用法:
        @router.post("/teams")
        @require_permission("team:create")
        def create_team(...): ...

        @router.post("/runs")
        @require_permission("run:create", team_name_param="team_name")
        def create_run(req: CreateRunRequest, ...): ...

    行为:
    - 未启用鉴权(request.state.user 不存在):直接放行(开发态)
    - 启用鉴权:检查 user 是否有 action 权限
      - team_name_param 指定时,从请求体/路径参数取 team_name 做 team-scoped 检查
    - 权限不足抛 403 HTTPException
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # 从参数中找 Request 对象
            request: Request | None = None
            for a in args:
                if isinstance(a, Request):
                    request = a
                    break
            if request is None:
                request = kwargs.get("request")

            user = getattr(request.state, "user", None) if request else None
            if user is None:
                # 未启用鉴权或未通过 RBAC 路径(legacy 模式),直接放行
                return await func(*args, **kwargs) if _is_async(func) else func(*args, **kwargs)

            # 提取 team_name(若声明)
            team_name: str | None = None
            if team_name_param:
                team_name = kwargs.get(team_name_param)

            # admin 通配
            user_roles = user.get("roles", [])
            for role_name, _team in user_roles:
                if role_name == "admin":
                    return await func(*args, **kwargs) if _is_async(func) else func(*args, **kwargs)

            # 查 permission 表
            from agentteam.api.deps import get_user_repo
            user_repo = get_user_repo()
            if user_repo is None:
                # user_repo 不可用(配置异常),保守拒绝
                raise HTTPException(status_code=503, detail="Permission service unavailable")

            if not user_repo.check_permission(user["id"], action, team_name):
                raise HTTPException(
                    status_code=403,
                    detail=f"Permission denied: requires '{action}'"
                    + (f" on team '{team_name}'" if team_name else ""),
                )
            return await func(*args, **kwargs) if _is_async(func) else func(*args, **kwargs)
        return wrapper
    return decorator


def _is_async(func) -> bool:
    import inspect
    return inspect.iscoroutinefunction(func)
