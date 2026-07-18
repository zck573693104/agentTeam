"""BUG-09: install_preset_to_api PUT 失败时错误信息应包含 PUT 响应。"""
from unittest.mock import MagicMock, patch


def _fake_response(status_code: int, json_data: dict | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = str(json_data or {})
    resp.json.return_value = json_data or {}
    return resp


def test_install_preset_put_failure_includes_put_error_info():
    """POST 400 + PUT 500 → RuntimeError 包含 PUT 的 500 和错误信息。"""
    from agentteam.presets import install_preset_to_api
    with patch("agentteam.presets.installer.requests") as mock_req:
        mock_req.post.return_value = _fake_response(400, {"detail": "already exists"})
        mock_req.put.return_value = _fake_response(500, {"detail": "DB write failed"})
        mock_req.ConnectionError = Exception
        try:
            install_preset_to_api("customer_support", api="http://api")
            assert False, "应抛 RuntimeError"
        except RuntimeError as e:
            msg = str(e)
            # POST 信息
            assert "400" in msg
            # PUT 信息(BUG-09 修复后应有)
            assert "500" in msg or "DB write failed" in msg, \
                f"PUT 错误信息丢失: {msg}"


def test_install_preset_post_500_no_put_fallback():
    """POST 500(非 400)→ 不回退 PUT,错误信息含 POST 500。"""
    from agentteam.presets import install_preset_to_api
    with patch("agentteam.presets.installer.requests") as mock_req:
        mock_req.post.return_value = _fake_response(500, {"detail": "server error"})
        mock_req.ConnectionError = Exception
        try:
            install_preset_to_api("customer_support", api="http://api")
            assert False, "应抛 RuntimeError"
        except RuntimeError as e:
            assert "500" in str(e)
        # 不应调用 PUT
        assert mock_req.put.call_count == 0
