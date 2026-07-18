"""CLI preset 命令测试。"""
from unittest.mock import MagicMock, patch

from agentteam.cli import main


def test_cli_list_presets_returns_zero(capsys):
    """list-presets 命令返回 0,stdout 含 4 个 preset name。"""
    rc = main(["list-presets"])
    assert rc == 0
    out = capsys.readouterr().out
    for name in ("enterprise_dev", "customer_support",
                 "data_analysis", "content_marketing"):
        assert name in out


def test_cli_show_preset_existing_returns_zero(capsys):
    """show-preset enterprise_dev 返回 0,stdout 含主团队名。"""
    rc = main(["show-preset", "enterprise_dev"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "enterprise_dev" in out
    assert "code_engineer" in out  # LIB_AGENTS 中的 agent


def test_cli_show_preset_nonexistent_returns_one(capsys):
    """show-preset nonexistent 返回 1,stdout 含错误信息。"""
    rc = main(["show-preset", "nonexistent"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "nonexistent" in out
    assert "Available" in out or "错误" in out


def test_cli_install_preset_calls_install_helper():
    """install-preset 调用 install_preset_to_api,成功返回 0。"""
    with patch("agentteam.cli.install_preset_to_api") as mock_install:
        mock_install.return_value = {"library": ["code_engineer"], "teams": ["enterprise_dev"]}
        rc = main(["install-preset", "enterprise_dev", "--api", "http://test"])
    assert rc == 0
    mock_install.assert_called_once_with("enterprise_dev", api="http://test")


def test_cli_install_preset_keyerror_returns_one(capsys):
    """install-preset 不存在 preset 返回 1。"""
    with patch("agentteam.cli.install_preset_to_api") as mock_install:
        mock_install.side_effect = KeyError("not found")
        rc = main(["install-preset", "nonexistent"])
    assert rc == 1


def test_cli_install_preset_runtimeerror_returns_one(capsys):
    """install-preset 安装失败(RuntimeError)返回 1。"""
    with patch("agentteam.cli.install_preset_to_api") as mock_install:
        mock_install.side_effect = RuntimeError("connection failed")
        rc = main(["install-preset", "enterprise_dev"])
    assert rc == 1
