"""CLI 入口测试。"""
from unittest.mock import patch, MagicMock

import requests

from agentteam.cli import main


def test_register_dev_team_success(capsys):
    """register-dev-team 成功注册团队。"""
    with patch("agentteam.cli.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"name": "dev_team"})
        main(["register-dev-team", "--api", "http://localhost:8000"])
    captured = capsys.readouterr()
    assert "dev_team" in captured.out
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == "http://localhost:8000/api/teams"
    assert kwargs["json"]["name"] == "dev_team"


def test_register_dev_team_default_api(capsys):
    """默认 API 地址为 http://localhost:8000。"""
    with patch("agentteam.cli.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"name": "dev_team"})
        main(["register-dev-team"])
    args, _ = mock_post.call_args
    assert args[0] == "http://localhost:8000/api/teams"


def test_register_dev_team_api_error(capsys):
    """API 返回错误时输出错误信息。"""
    with patch("agentteam.cli.requests.post") as mock_post:
        mock_post.return_value = MagicMock(
            status_code=409,
            json=lambda: {"detail": "Team already exists"},
        )
        main(["register-dev-team"])
    captured = capsys.readouterr()
    assert "Team already exists" in captured.out


def test_register_dev_team_connection_error(capsys):
    """连接失败时输出错误信息。"""
    with patch("agentteam.cli.requests.post", side_effect=requests.ConnectionError("Connection refused")):
        main(["register-dev-team"])
    captured = capsys.readouterr()
    assert "错误" in captured.out


def test_register_team_command_calls_api(monkeypatch):
    """register-team 命令调用 POST /api/teams。"""
    import agentteam.cli as cli
    called = {}
    class FakeResp:
        status_code = 200
        def json(self): return {"name": "t"}
    def fake_post(url, json=None, timeout=None):
        called["url"] = url
        called["json"] = json
        return FakeResp()
    monkeypatch.setattr(cli.requests, "post", fake_post)
    # 模拟 importlib 动态加载模块
    monkeypatch.setattr(cli, "_load_team_module", lambda path: {
        "name": "t", "description": "d",
        "root": {"name": "lead", "role": "supervisor", "children": []},
        "default_model": {"provider": "qwen", "name": "qwen-max"},
    })
    rc = cli.main(["register-team", "some_file.py", "--api", "http://test"])
    assert rc == 0
    assert called["url"] == "http://test/api/teams"


def test_list_teams_command_calls_api(monkeypatch):
    """list-teams 命令调用 GET /api/teams。"""
    import agentteam.cli as cli
    class FakeResp:
        status_code = 200
        def json(self): return [{"name": "t1"}, {"name": "t2"}]
    def fake_get(url, timeout=None):
        return FakeResp()
    monkeypatch.setattr(cli.requests, "get", fake_get)
    rc = cli.main(["list-teams", "--api", "http://test"])
    assert rc == 0


def test_register_library_command_calls_api(monkeypatch):
    """register-library 命令调用 POST /api/library/agents。"""
    import agentteam.cli as cli
    called = {}
    class FakeResp:
        status_code = 200
        def json(self): return {"ok": True}
    def fake_post(url, json=None, timeout=None):
        called["url"] = url
        called["json"] = json
        return FakeResp()
    monkeypatch.setattr(cli.requests, "post", fake_post)
    monkeypatch.setattr(cli, "_load_library_module", lambda path: [
        {"name": "coder", "role": "worker", "system_prompt": "code"},
    ])
    rc = cli.main(["register-library", "lib.py", "--api", "http://test"])
    assert rc == 0
    assert called["url"] == "http://test/api/library/agents"
