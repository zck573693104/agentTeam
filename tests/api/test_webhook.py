"""P-A5 审批 webhook:fire_approval_webhook + payload 构造测试。"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

import pytest

from agentteam.api.webhook import (
    _mask_url,
    _truncate,
    build_approval_payload,
    fire_approval_webhook,
)


class TestTruncate:
    def test_short_value_preserved(self):
        assert _truncate("hello") == "hello"

    def test_long_value_truncated(self):
        long = "x" * 2000
        result = _truncate(long)
        assert len(result) < len(long)
        assert result.endswith("...(truncated)")
        assert result.startswith("x")

    def test_none_value(self):
        assert _truncate(None) == ""

    def test_int_value(self):
        assert _truncate(42) == "42"


class TestBuildApprovalPayload:
    def test_basic_payload(self):
        payload = build_approval_payload(
            run_id="r1",
            team_name="t1",
            gate="step",
            target="worker_a",
            message="please approve",
        )
        assert payload["event"] == "approval_requested"
        assert payload["run_id"] == "r1"
        assert payload["team_name"] == "t1"
        assert payload["gate"] == "step"
        assert payload["target"] == "worker_a"
        assert payload["message"] == "please approve"
        assert "timestamp" in payload

    def test_long_message_truncated(self):
        long_msg = "x" * 2000
        payload = build_approval_payload(
            run_id="r1", team_name="t1", gate="step", target="t", message=long_msg,
        )
        assert payload["message"].endswith("...(truncated)")
        assert len(payload["message"]) < len(long_msg)

    def test_long_target_truncated(self):
        long_target = "x" * 2000
        payload = build_approval_payload(
            run_id="r1", team_name="t1", gate="step", target=long_target, message="m",
        )
        assert payload["target"].endswith("...(truncated)")

    def test_none_target_serialized_as_empty(self):
        payload = build_approval_payload(
            run_id="r1", team_name="t1", gate="step", target=None, message="m",
        )
        assert payload["target"] == ""

    def test_payload_is_json_serializable(self):
        payload = build_approval_payload(
            run_id="r1", team_name="t1", gate="step", target="w", message="m",
        )
        # 必须能 JSON 序列化(用于 _post_json)
        s = json.dumps(payload, ensure_ascii=False)
        assert json.loads(s) == payload


class TestMaskUrl:
    def test_dingtalk_access_token_masked(self):
        url = "https://oapi.dingtalk.com/robot/send?access_token=abcdef123456"
        masked = _mask_url(url)
        assert "abcdef123456" not in masked
        assert "access_token=***" in masked

    def test_feishu_hook_masked(self):
        url = "https://open.feishu.cn/open-apis/bot/v2/hook/abcdef123456"
        masked = _mask_url(url)
        assert "abcdef123456" not in masked
        assert masked.endswith("/***")

    def test_plain_url_unchanged(self):
        url = "https://example.com/webhook"
        assert _mask_url(url) == url

    def test_empty_url(self):
        assert _mask_url("") == ""


class TestFireApprovalWebhook:
    def test_none_url_is_noop(self):
        """webhook_url=None 直接跳过,不启动线程。"""
        # 应该不抛异常,立即返回
        fire_approval_webhook(None, "r1", "t1", "step", "w", "msg")

    def test_empty_url_is_noop(self):
        fire_approval_webhook("", "r1", "t1", "step", "w", "msg")

    def test_failure_does_not_raise(self):
        """webhook POST 失败应仅记日志,不抛异常。"""
        # 用一个不可能连上的 url,触发 URLError
        fire_approval_webhook(
            "http://127.0.0.1:1/nonexistent",
            "r1", "t1", "step", "w", "msg",
        )
        # 等 daemon 线程结束(给足时间)
        time.sleep(0.5)

    def test_successful_post_hits_endpoint(self):
        """启动一个本地 HTTP server,验证 webhook 真的 POST 了。"""
        received: list[dict] = []
        server_ready = threading.Event()
        server_port: list[int] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8")
                received.append(json.loads(body))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, *args, **kwargs):
                pass  # 静默

        def _run_server():
            server = HTTPServer(("127.0.0.1", 0), Handler)
            server_port.append(server.server_address[1])
            server_ready.set()
            server.handle_request()  # 只处理一个请求
            server.server_close()

        t = threading.Thread(target=_run_server, daemon=True)
        t.start()
        server_ready.wait(timeout=2.0)
        assert server_port, "server did not start"

        url = f"http://127.0.0.1:{server_port[0]}/webhook"
        fire_approval_webhook(url, "r1", "t1", "step", "worker_a", "please approve")

        # 等待 daemon 线程完成 POST(server.handle_request 一次就退出)
        t.join(timeout=3.0)
        assert len(received) == 1
        payload = received[0]
        assert payload["event"] == "approval_requested"
        assert payload["run_id"] == "r1"
        assert payload["team_name"] == "t1"
        assert payload["gate"] == "step"
        assert payload["target"] == "worker_a"
        assert payload["message"] == "please approve"
