"""CLI 入口测试(v2 命令)。"""
import json
from unittest.mock import patch, MagicMock

from agentteam.cli import main


def _mock_post():
    m = MagicMock()
    m.return_value = MagicMock(status_code=200, json=lambda: {"name": "coder"})
    return m


def test_submit_request(capsys):
    """submit 走 POST /api/requests。"""
    with patch("agentteam.cli.requests.request") as mock_req:
        mock_req.return_value = MagicMock(status_code=200, json=lambda: {"action": "dispatch",
                                                                         "ticket_id": "t1"})
        main(["submit", "干活", "coder"])
    captured = capsys.readouterr()
    assert "dispatch" in captured.out
    args, kwargs = mock_req.call_args
    assert args == ("POST", "http://localhost:8000/api/requests")
    assert kwargs["json"]["worker_name"] == "coder"


def test_tickets_command(capsys):
    with patch("agentteam.cli.requests.request") as mock_req:
        mock_req.return_value = MagicMock(status_code=200,
                                          json=lambda: [{"id": "a" * 32, "status": "done",
                                                         "title": "t", "worker_name": "coder"}])
        main(["tickets", "done"])
    captured = capsys.readouterr()
    assert "[done]" in captured.out
    args, _ = mock_req.call_args
    assert args[1].endswith("/api/tickets?status=done")


def test_install_preset_registers_workers(capsys, tmp_path):
    """install-preset 逐个注册 WORKERS(幂等 upsert)。"""
    with patch("agentteam.cli.requests.request") as mock_req:
        mock_req.return_value = MagicMock(status_code=200, json=lambda: {"name": "coder"})
        main(["install-preset", "dev_pool"])
    captured = capsys.readouterr()
    assert "研发工人池" in captured.out  # METADATA.title
    calls = [c for c in mock_req.call_args_list if c.args[1].endswith("/api/workers")]
    assert len(calls) == 2  # dev_pool: coder + tester


def test_install_preset_missing(capsys):
    import pytest
    with pytest.raises(SystemExit):
        main(["install-preset", "no_such_preset"])
