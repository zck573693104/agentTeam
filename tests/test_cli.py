"""CLI 入口测试。"""
from unittest.mock import patch, MagicMock

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
    assert "错误" in captured.out or "Team already exists" in captured.out


def test_register_dev_team_connection_error(capsys):
    """连接失败时输出错误信息。"""
    with patch("agentteam.cli.requests.post", side_effect=ConnectionError("Connection refused")):
        main(["register-dev-team"])
    captured = capsys.readouterr()
    assert "错误" in captured.out
