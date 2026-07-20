"""P-A2 API 鉴权:ApiKeyMiddleware + setup_auth 测试。"""
from __future__ import annotations

import os
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentteam.api.auth import ApiKeyMiddleware, setup_auth, _is_exempt, _parse_api_keys


def _build_app(valid_keys: list[str]) -> FastAPI:
    """构造一个装了 ApiKeyMiddleware 的 app,带一个 /api/ping 端点。"""
    app = FastAPI()

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/")
    def root():
        return {"name": "root"}

    app.add_middleware(ApiKeyMiddleware, valid_keys=valid_keys)
    return app


class TestParseApiKeys:
    def test_simple(self):
        assert _parse_api_keys("k1,k2,k3") == {"k1", "k2", "k3"}

    def test_whitespace_trimmed(self):
        assert _parse_api_keys(" k1 , k2 ,k3 ") == {"k1", "k2", "k3"}

    def test_empty_entries_dropped(self):
        assert _parse_api_keys("k1,,k2,") == {"k1", "k2"}

    def test_empty_string(self):
        assert _parse_api_keys("") == set()


class TestIsExempt:
    def test_root_exempt(self):
        assert _is_exempt("/") is True

    def test_health_exempt(self):
        assert _is_exempt("/api/health") is True

    def test_docs_exempt(self):
        assert _is_exempt("/docs") is True

    def test_openapi_exempt(self):
        assert _is_exempt("/openapi.json") is True

    def test_redoc_exempt(self):
        assert _is_exempt("/redoc") is True

    def test_static_exempt(self):
        assert _is_exempt("/static/index.js") is True

    def test_api_not_exempt(self):
        assert _is_exempt("/api/teams") is False

    def test_arbitrary_path_not_exempt(self):
        assert _is_exempt("/api/runs/abc") is False


class TestApiKeyMiddleware:
    def test_valid_key_passes(self):
        app = _build_app(["secret-key-1"])
        client = TestClient(app)
        resp = client.get("/api/ping", headers={"X-API-Key": "secret-key-1"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_missing_key_returns_401(self):
        app = _build_app(["secret-key-1"])
        client = TestClient(app)
        resp = client.get("/api/ping")
        assert resp.status_code == 401
        assert "X-API-Key" in resp.json()["detail"]

    def test_wrong_key_returns_401(self):
        app = _build_app(["secret-key-1"])
        client = TestClient(app)
        resp = client.get("/api/ping", headers={"X-API-Key": "wrong"})
        assert resp.status_code == 401

    def test_empty_key_returns_401(self):
        app = _build_app(["secret-key-1"])
        client = TestClient(app)
        resp = client.get("/api/ping", headers={"X-API-Key": ""})
        assert resp.status_code == 401

    def test_exempt_path_no_key_required(self):
        app = _build_app(["secret-key-1"])
        client = TestClient(app)
        # /api/health 豁免
        resp = client.get("/api/health")
        assert resp.status_code == 200
        # / 豁免
        resp = client.get("/")
        assert resp.status_code == 200

    def test_multiple_valid_keys(self):
        app = _build_app(["k1", "k2", "k3"])
        client = TestClient(app)
        for k in ("k1", "k2", "k3"):
            resp = client.get("/api/ping", headers={"X-API-Key": k})
            assert resp.status_code == 200, f"key {k} should pass"

    def test_non_api_path_passes_through(self):
        """非 /api/ 路径(前端静态资源)直接放行,不需要 key。"""
        app = _build_app(["secret-key-1"])
        client = TestClient(app)
        # / 非前缀匹配 /api/,放行
        resp = client.get("/")
        assert resp.status_code == 200

    def test_empty_valid_keys_blocks_all(self):
        """valid_keys 为空 → 任何请求都被拒(包括正确 key)。"""
        app = _build_app([])
        client = TestClient(app)
        # 即便带了 key,因 valid_keys 为空,仍然 401
        resp = client.get("/api/ping", headers={"X-API-Key": "any"})
        assert resp.status_code == 401
        # 但豁免路径仍可访问
        resp = client.get("/api/health")
        assert resp.status_code == 200


class TestSetupAuth:
    def teardown_method(self):
        # 清理 env
        for k in ("AGENTTEAM_AUTH_ENABLED", "AGENTTEAM_AUTH_API_KEYS"):
            os.environ.pop(k, None)
        # 重置 settings 单例(下次 get_settings() 重新读环境变量)
        import agentteam.config as cfg
        cfg._settings_instance = None

    def test_disabled_by_default(self):
        """auth_enabled 默认 False,setup_auth 不安装中间件。"""
        os.environ.pop("AGENTTEAM_AUTH_ENABLED", None)
        import agentteam.config as cfg
        cfg._settings_instance = None
        app = FastAPI()
        enabled = setup_auth(app)
        assert enabled is False

    def test_enabled_but_no_keys_uses_never_match(self):
        """启用但未配置 key → 安装中间件,但占位 key 任何请求都不匹配。"""
        os.environ["AGENTTEAM_AUTH_ENABLED"] = "true"
        os.environ.pop("AGENTTEAM_AUTH_API_KEYS", None)
        import agentteam.config as cfg
        cfg._settings_instance = None
        app = FastAPI()

        @app.get("/api/ping")
        def ping():
            return {"ok": True}

        enabled = setup_auth(app)
        assert enabled is True
        client = TestClient(app)
        # 配置错误 → 所有 /api 请求 401
        resp = client.get("/api/ping", headers={"X-API-Key": "any"})
        assert resp.status_code == 401

    def test_enabled_with_keys_installs_middleware(self):
        os.environ["AGENTTEAM_AUTH_ENABLED"] = "true"
        os.environ["AGENTTEAM_AUTH_API_KEYS"] = "valid-key-1,valid-key-2"
        import agentteam.config as cfg
        cfg._settings_instance = None
        app = FastAPI()

        @app.get("/api/ping")
        def ping():
            return {"ok": True}

        enabled = setup_auth(app)
        assert enabled is True
        client = TestClient(app)
        # 合法 key 通过
        resp = client.get("/api/ping", headers={"X-API-Key": "valid-key-1"})
        assert resp.status_code == 200
        resp = client.get("/api/ping", headers={"X-API-Key": "valid-key-2"})
        assert resp.status_code == 200
        # 非法 key 401
        resp = client.get("/api/ping", headers={"X-API-Key": "invalid"})
        assert resp.status_code == 401
        # 无 key 401
        resp = client.get("/api/ping")
        assert resp.status_code == 401
