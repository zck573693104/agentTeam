"""install_preset_to_api 测试(mocked HTTP)。"""
from unittest.mock import MagicMock, patch


def _fake_response(status_code: int, json_data: dict | None = None):
    """构造 fake requests.Response。"""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = str(json_data or {})
    resp.json.return_value = json_data or {}
    return resp


def test_install_preset_nonexistent_raises_keyerror():
    """install 不存在的 preset 抛 KeyError。"""
    from agentteam.presets import install_preset_to_api
    try:
        install_preset_to_api("nonexistent")
        assert False, "应抛 KeyError"
    except KeyError as e:
        assert "nonexistent" in str(e)


def test_install_preset_no_deps_posts_only_team():
    """无 deps 的 preset(customer_support):只 POST 1 次 team,无 library/sub-team。"""
    from agentteam.presets import install_preset_to_api
    with patch("agentteam.presets.installer.requests") as mock_req:
        mock_req.post.return_value = _fake_response(200, {"name": "customer_support"})
        mock_req.ConnectionError = Exception  # patch class attr
        result = install_preset_to_api("customer_support", api="http://api")
    # 仅 POST team 1 次,无 library/sub-team POST
    assert mock_req.post.call_count == 1
    called_url = mock_req.post.call_args[0][0]
    assert called_url == "http://api/api/teams"
    assert result["teams"] == ["customer_support"]
    assert result["library"] == []


def test_install_preset_with_library_posts_library_then_team():
    """enterprise_dev:先 POST library(code_engineer),再 POST sub-team,再 POST team。"""
    from agentteam.presets import install_preset_to_api
    with patch("agentteam.presets.installer.requests") as mock_req:
        mock_req.post.return_value = _fake_response(200, {"name": "x"})
        mock_req.ConnectionError = Exception
        result = install_preset_to_api("enterprise_dev", api="http://api")
    # POST 顺序:library(1) + sub-team(1) + team(1) = 3 次
    assert mock_req.post.call_count == 3
    # 第 1 次:library
    first_url = mock_req.post.call_args_list[0][0][0]
    assert first_url == "http://api/api/library/agents"
    # 第 2 次:sub-team (test_subteam)
    second_url = mock_req.post.call_args_list[1][0][0]
    assert second_url == "http://api/api/teams"
    second_payload = mock_req.post.call_args_list[1][1]["json"]
    assert second_payload["name"] == "test_subteam"
    # 第 3 次:主 team
    third_payload = mock_req.post.call_args_list[2][1]["json"]
    assert third_payload["name"] == "enterprise_dev"
    assert result["library"] == ["code_engineer"]
    assert "test_subteam" in result["teams"]
    assert "enterprise_dev" in result["teams"]


def test_install_preset_duplicate_falls_back_to_put():
    """POST 返回 400(重复)→ 回退 PUT,实现幂等。"""
    from agentteam.presets import install_preset_to_api
    with patch("agentteam.presets.installer.requests") as mock_req:
        # POST 返回 400,PUT 返回 200
        mock_req.post.return_value = _fake_response(400, {"detail": "already exists"})
        mock_req.put.return_value = _fake_response(200, {"name": "customer_support"})
        mock_req.ConnectionError = Exception
        result = install_preset_to_api("customer_support", api="http://api")
    # POST 1 次 + PUT 1 次
    assert mock_req.post.call_count == 1
    assert mock_req.put.call_count == 1
    put_url = mock_req.put.call_args[0][0]
    assert put_url == "http://api/api/teams/customer_support"
    assert result["teams"] == ["customer_support(updated)"]


def test_install_preset_post_5xx_raises_runtimeerror():
    """POST 返回 500(非重复)→ 抛 RuntimeError,不回退 PUT。"""
    from agentteam.presets import install_preset_to_api
    with patch("agentteam.presets.installer.requests") as mock_req:
        mock_req.post.return_value = _fake_response(500, {"detail": "server error"})
        mock_req.ConnectionError = Exception
        try:
            install_preset_to_api("customer_support", api="http://api")
            assert False, "应抛 RuntimeError"
        except RuntimeError as e:
            assert "500" in str(e) or "server error" in str(e)
    # 不应回退 PUT
    assert mock_req.put.call_count == 0


def test_install_preset_engine_override_applies_to_all_teams():
    """SP8.1: engine="opencode" 覆盖主团队与 deps_teams 的执行引擎。"""
    from agentteam.presets import install_preset_to_api
    with patch("agentteam.presets.installer.requests") as mock_req:
        mock_req.post.return_value = _fake_response(200, {"name": "x"})
        mock_req.ConnectionError = Exception
        result = install_preset_to_api("enterprise_dev", api="http://api",
                                       engine="opencode")
    team_posts = [
        c[1]["json"] for c in mock_req.post.call_args_list
        if c[0][0].endswith("/api/teams")
    ]
    assert len(team_posts) == 2  # sub-team + 主团队
    assert all(t["engine"] == "opencode" for t in team_posts)
    assert result["engine"] == "opencode"


def test_install_preset_engine_none_keeps_preset_default():
    """engine=None：payload 不含 engine 键（预设未声明 → langgraph 默认）。"""
    from agentteam.presets import install_preset_to_api
    with patch("agentteam.presets.installer.requests") as mock_req:
        mock_req.post.return_value = _fake_response(200, {"name": "x"})
        mock_req.ConnectionError = Exception
        install_preset_to_api("customer_support", api="http://api")
    payload = mock_req.post.call_args[1]["json"]
    assert "engine" not in payload


def test_install_preset_engine_override_roundtrip_via_api(tmp_path):
    """端到端：engine 覆盖经 team_from_dict 落库，GET /api/teams 可见。"""
    from fastapi.testclient import TestClient

    from agentteam.api.server import create_app
    from agentteam.presets import install_preset_to_api
    from agentteam.tools.registry import ToolRegistry
    from tests.conftest import FakeLLM, FakeModelProvider

    app = create_app(
        db_path=str(tmp_path / "preset.db"),
        model_provider=FakeModelProvider({"qwen-max": FakeLLM()}),
        tool_registry=ToolRegistry(),
        web_dist=None,
        harness_enabled=False,
    )
    client = TestClient(app)
    with patch("agentteam.presets.installer.requests") as mock_req:
        # 把 installer 的 HTTP 调用转到 TestClient（同进程，无需起服务）
        import requests as real_requests

        def _route(method, url, json=None, timeout=None):
            path = url.split("http://api", 1)[1]
            resp = MagicMock()
            r = client.post(path, json=json) if method == "post" \
                else client.put(path, json=json)
            resp.status_code = r.status_code
            resp.text = r.text
            resp.json.return_value = r.json() if r.content else {}
            return resp

        mock_req.post.side_effect = lambda url, json=None, timeout=None: _route(
            "post", url, json, timeout)
        mock_req.put.side_effect = lambda url, json=None, timeout=None: _route(
            "put", url, json, timeout)
        mock_req.ConnectionError = Exception
        result = install_preset_to_api("customer_support", api="http://api",
                                       engine="opencode")
    assert result["teams"] == ["customer_support"]
    got = client.get("/api/teams/customer_support").json()
    assert got["engine"] == "opencode"
    # 引擎字段真实参与运行时解析（_team_engine 读 team.engine）
    from agentteam.domain.serializer import team_from_dict
    assert team_from_dict(got).engine == "opencode"
