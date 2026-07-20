"""API Key 鉴权中间件(对标阿里云 AgentTeams "访问控制")。

设计:
- 默认关闭(auth_enabled=false),开发态零配置可启动
- 启用后所有 /api/* 请求必须带 X-API-Key header,匹配 auth_api_keys(逗号分隔)中之一
- GET /api/health 与 / 路径(前端静态)豁免
- 鉴权失败返回 401 + JSON {"detail": "..."},与 FastAPI HTTPException 风格一致

环境变量:
- AGENTTEAM_AUTH_ENABLED=true|false
- AGENTTEAM_AUTH_API_KEYS=key1,key2,key3

用法:
    from agentteam.api.auth import setup_auth
    setup_auth(app)  # 根据 Settings 决定是否启用
"""
from __future__ import annotations

import secrets
from typing import Iterable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from agentteam.config import get_settings

# 豁免路径前缀/精确匹配(无需鉴权)
# - /api/health: 健康检查(若存在)
# - /docs, /openapi.json, /redoc: FastAPI 自动文档
# - /: 前端静态资源
_EXEMPT_PATH_PREFIXES = ("/docs", "/redoc", "/openapi.json", "/static")
_EXEMPT_PATHS_EXACT = {"/", "/api/health"}


def _parse_api_keys(raw: str) -> set[str]:
    """解析逗号分隔的 API key 列表,去掉空白与空串。"""
    return {k.strip() for k in raw.split(",") if k.strip()}


def _is_exempt(path: str) -> bool:
    if path in _EXEMPT_PATHS_EXACT:
        return True
    return any(path.startswith(p) for p in _EXEMPT_PATH_PREFIXES)


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """X-API-Key header 校验中间件。

    使用 secrets.compare_digest 做常数时间比较,避免计时攻击。
    """

    def __init__(self, app, valid_keys: Iterable[str]) -> None:
        super().__init__(app)
        self._valid_keys = set(valid_keys)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if _is_exempt(path):
            return await call_next(request)
        # 非 /api/* 路径(前端静态资源挂载点)直接放行
        if not path.startswith("/api/"):
            return await call_next(request)
        provided = request.headers.get("X-API-Key", "")
        # 常数时间比较:对每个合法 key 都 compare_digest,避免计时侧信道
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


def setup_auth(app: FastAPI) -> bool:
    """根据 Settings 决定是否安装 ApiKeyMiddleware,返回是否启用。

    在 create_app 中调用。auth_enabled=false 或 auth_api_keys 为空时静默跳过,
    保持开发态零配置启动的体验。
    """
    settings = get_settings()
    if not settings.auth_enabled:
        return False
    valid_keys = _parse_api_keys(settings.auth_api_keys)
    if not valid_keys:
        # 启用鉴权但未配置任何 key:这是配置错误,但为安全计拒绝所有请求
        # (而非退化为不鉴权)。日志由调用方记录。
        valid_keys = {"__never_match__"}  # 占位 key,任何请求都不匹配
    app.add_middleware(ApiKeyMiddleware, valid_keys=valid_keys)
    return True
