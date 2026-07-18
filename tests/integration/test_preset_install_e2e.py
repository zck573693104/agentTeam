"""预置团队安装 E2E 测试 — TestClient + 真实 API + 真实 DB。

验证:
- install_preset_to_api 通过 HTTP 安装到 TestClient 启动的 API
- 安装后 GET /api/teams/{name} 返回 200
- 依赖的 library agent 与 sub-team 也注册成功
- 重复安装幂等(POST→PUT 回退)
"""
from pathlib import Path

from fastapi.testclient import TestClient

from agentteam.api.server import create_app
from agentteam.presets import install_preset_to_api


def _install_via_testclient(name: str, client: TestClient) -> dict:
    """用 TestClient 的 transport 替代 requests,调用 install_preset_to_api。"""
    # TestClient 的 base_url 默认为 http://testserver
    # install_preset_to_api 内部用 requests.post/put,需 patch 为 client.post/put
    from unittest.mock import patch

    def _post(url, json=None, timeout=None):
        path = url.replace("http://testserver", "")
        return client.post(path, json=json)

    def _put(url, json=None, timeout=None):
        path = url.replace("http://testserver", "")
        return client.put(path, json=json)

    with patch("agentteam.presets.installer.requests") as mock_req:
        mock_req.post.side_effect = _post
        mock_req.put.side_effect = _put
        mock_req.ConnectionError = Exception
        return install_preset_to_api(name, api="http://testserver")


def test_install_enterprise_dev_e2e(tmp_path: Path):
    """安装 enterprise_dev:library + sub-team + 主 team 都注册成功。"""
    db_path = tmp_path / "test.db"
    app = create_app(db_path=str(db_path), web_dist=None)
    client = TestClient(app)

    result = _install_via_testclient("enterprise_dev", client)
    assert "code_engineer" in result["library"]
    assert "test_subteam" in result["teams"]
    assert "enterprise_dev" in result["teams"]

    # 验证 GET
    assert client.get("/api/teams/enterprise_dev").status_code == 200
    assert client.get("/api/teams/test_subteam").status_code == 200
    assert client.get("/api/library/agents").status_code == 200
    agents = client.get("/api/library/agents").json()
    assert any(a["name"] == "code_engineer" for a in agents)


def test_install_customer_support_e2e(tmp_path: Path):
    """安装 customer_support(无 deps):只注册主 team。"""
    db_path = tmp_path / "test.db"
    app = create_app(db_path=str(db_path), web_dist=None)
    client = TestClient(app)

    result = _install_via_testclient("customer_support", client)
    assert result["library"] == []
    assert "customer_support" in result["teams"]
    assert client.get("/api/teams/customer_support").status_code == 200


def test_install_is_idempotent(tmp_path: Path):
    """重复安装 enterprise_dev:第二次 POST→PUT 回退,仍成功。"""
    db_path = tmp_path / "test.db"
    app = create_app(db_path=str(db_path), web_dist=None)
    client = TestClient(app)

    # 第一次安装
    result1 = _install_via_testclient("enterprise_dev", client)
    assert "enterprise_dev" in result1["teams"]

    # 第二次安装(应触发 PUT 回退)
    result2 = _install_via_testclient("enterprise_dev", client)
    # 第二次 teams 列表中应有 "(updated)" 标记
    assert any("updated" in t for t in result2["teams"])

    # GET 仍正常
    assert client.get("/api/teams/enterprise_dev").status_code == 200


def test_install_all_four_presets(tmp_path: Path):
    """安装全部 4 个 preset,各自 GET 200。"""
    db_path = tmp_path / "test.db"
    app = create_app(db_path=str(db_path), web_dist=None)
    client = TestClient(app)

    for name in ("enterprise_dev", "customer_support",
                 "data_analysis", "content_marketing"):
        result = _install_via_testclient(name, client)
        assert name in result["teams"], f"{name} 安装失败: {result}"
        assert client.get(f"/api/teams/{name}").status_code == 200
