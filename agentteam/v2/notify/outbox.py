"""v2 通知出站:OutboxWorker(先存后发/退避重试/同工单串行/重启续发)+ 消息预算。

移植自 v1 notify/outbox.py + SP12a 消息预算,run_id → ticket_id。
dsh Effects 语义:先存后做,幂等,失败退避,连续失败置 dropped 可查回执。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable

import requests

from agentteam.logging_config import get_logger
from agentteam.v2.domain import events as ev
from agentteam.v2.storage.outbox import OutboxRepo

logger = get_logger("v2.notify.outbox")

_MAX_BACKOFF_SECONDS = 300.0
_ERROR_SNIPPET = 500


def send_webhook(url: str, body: dict, timeout: float = 5.0) -> None:
    resp = requests.post(url, json=body, timeout=timeout)
    if resp.status_code >= 300:
        raise RuntimeError(f"webhook HTTP {resp.status_code}")


class OutboxWorker:
    """后台轮询消费 outbox(2s);同工单 FIFO 串行;指数退避 5s→300s;5 败 → dropped。"""

    def __init__(
        self,
        repo: OutboxRepo,
        *,
        sender: Callable[[str, dict], None] | None = None,
        retry_max: int = 5,
        backoff_base: float = 5.0,
        poll_interval: float = 2.0,
        send_timeout: float = 5.0,
    ) -> None:
        self._repo = repo
        self._sender = sender or send_webhook
        self._retry_max = retry_max
        self._backoff_base = backoff_base
        self._poll_interval = poll_interval
        self._send_timeout = send_timeout
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="v2-outbox", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:  # 轮询主循环永不退出
                logger.exception("outbox poll failed")
            self._stop.wait(self._poll_interval)

    def run_once(self, now: float | None = None) -> bool:
        """处理一批到期行(纯函数可测)。返回是否发送过。"""
        now = now if now is not None else time.time()
        sent_any = False
        rows = self._repo.pending_rows()
        # 同工单串行:按 (ticket_id, id) 分组,前一行为 pending 时后续不发送
        blocked_tickets: set[str] = set()
        for row in rows:
            ticket_id = row["ticket_id"]
            if row["next_retry_at"] > now:
                continue
            if ticket_id in blocked_tickets:
                continue
            try:
                self._sender(row["url"], json.loads(row["payload_json"]))
                self._repo.mark_sent(row["id"], now)
                sent_any = True
            except Exception as exc:
                attempts = row["attempts"] + 1
                if attempts >= self._retry_max:
                    self._repo.mark_dropped(row["id"], attempts, str(exc))
                else:
                    backoff = min(self._backoff_base * (2 ** (attempts - 1)),
                                  _MAX_BACKOFF_SECONDS)
                    self._repo.mark_retry(row["id"], attempts, now + backoff, str(exc))
                    blocked_tickets.add(ticket_id)  # 串行:失败后同工单后续等待
        return sent_any


def format_message(event: str, payload: dict, console_url: str) -> dict:
    """通用 webhook 消息体(SP12a 预算:正文 ≤300 字符 + 尾行控制台链接)。"""
    title = {
        ev.TICKET_STATUS_CHANGED: f"工单 {payload.get('to', '')}",
        ev.ATTENTION_CREATED: "需要人工确认",
        ev.TICKET_CANCELLED: "工单已撤权",
    }.get(event, event)
    text_parts = [str(payload.get("note") or payload.get("error") or
                      payload.get("question") or payload.get("result") or title)]
    text = "\n".join(text_parts)[:300]
    ticket_id = payload.get("ticket_id") or ""
    if len(text_parts[0]) > 300:
        text += "\n…(完整内容见控制台)"
    return {
        "msg_type": "text",
        "content": {
            "title": title,
            "text": f"{text}\n{console_url}/v2/tickets/{ticket_id}" if ticket_id else text,
        },
    }
