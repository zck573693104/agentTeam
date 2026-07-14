"""测试 FastAPI 静态文件挂载不影响 API。"""
from fastapi.testclient import TestClient

from agentteam.api.server import create_app


def test_api_works_without_web_dist(tmp_path):
    """web/dist 不存在时 API 正常工作。"""
    app = create_app(db_path=str(tmp_path / "test.db"))
    client = TestClient(app)
    resp = client.get("/api/dashboard")
    assert resp.status_code == 200


def test_api_routes_not_shadowed(tmp_path):
    """静态文件挂载(若存在)不影响 /api/* 路由。"""
    app = create_app(db_path=str(tmp_path / "test.db"))
    client = TestClient(app)
    # 多个 API 端点都应正常响应
    assert client.get("/api/teams").status_code == 200
    assert client.get("/api/runs").status_code == 200
    assert client.get("/api/dashboard").status_code == 200
