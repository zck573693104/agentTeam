"""测试 FastAPI 静态文件挂载不影响 API。"""
from fastapi.testclient import TestClient

from agentteam.api.server import create_app


def test_api_works_without_web_dist(tmp_path):
    """显式禁用静态挂载时 API 正常工作。"""
    app = create_app(db_path=str(tmp_path / "test.db"), web_dist=None)
    client = TestClient(app)
    resp = client.get("/api/dashboard")
    assert resp.status_code == 200


def test_web_dist_mounted_when_dir_exists(tmp_path):
    """web_dist 指向存在的目录时,前端静态文件被挂载到根路径。"""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>hello</html>", encoding="utf-8")

    app = create_app(db_path=str(tmp_path / "test.db"), web_dist=dist)
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "hello" in resp.text


def test_api_routes_not_shadowed(tmp_path):
    """静态文件挂载存在时也不影响 /api/* 路由。"""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>shadow</html>", encoding="utf-8")

    app = create_app(db_path=str(tmp_path / "test.db"), web_dist=dist)
    client = TestClient(app)
    # 多个 API 端点都应正常响应,即使根路径已挂载静态文件
    assert client.get("/api/teams").status_code == 200
    assert client.get("/api/runs").status_code == 200
    assert client.get("/api/dashboard").status_code == 200
