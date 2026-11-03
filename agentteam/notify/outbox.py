"""IM 通知 outbox 后台发送器(SP12b)。

语义对齐 dsh-superagent Outbox(简化取舍见 spec §4.2):
- 先存后发:enqueue 落库(NotifyOutboxRepo),本 worker 只消费;
- 退避重试:失败后 5s 起步指数翻倍,上限 300s;连续 RETRY_MAX 次失败
  置 dropped(保留 last_error 审计;dsh 原语义是无限重试,这里简化为
  有限次 + 落库可查);
- 同 run 串行:同一 run 的消息按入队序发送,前一条未发出(且未达
  失败上限)时后续消息一律等待——保证「已收到」先于结果到达;
- 恢复:进程重启后 pending 行自动续发(daemon 启动即扫表)。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable

import requests

from agentteam.logging_config import get_logger
from agentteam.storage.notify import NotifyOutboxRepo

logger = get_logger("api.notify.outbox")

# 退避上限(秒);起点与倍率由 Settings.notify_backoff_base 控制
_MAX_BACKOFF_SECONDS = 300.0
# 错误摘要落库长度
_ERROR_SNIPPET = 500


def send_webhook(url: str, body: dict, timeout: float) -> None:
    """同步发送一条 webhook 通知;HTTP >= 300 或网络异常都抛错。

    成功(2xx)静默返回。供 OutboxWorker 与 NotificationService 旧路径共用。
    """
    resp = requests.post(url, json=body, timeout=timeout)
    if resp.status_code >= 300:
        raise RuntimeError(f"webhook HTTP {resp.status_code}")


class OutboxWorker:
    """notify_outbox 消费者:2s 轮询到期 pending 行,串行发送。

    run_once 无 I/O 隐藏逻辑(时间由调用方注入),单测直接驱动;
    start/stop 是 daemon 线程壳。发送回调 sender(url, body) 抛错即失败。
    """

    def __init__(
        self,
        repo: NotifyOutboxRepo,
        *,
        sender: Callable[[str, dict], None] | None = None,
        retry_max: int = 5,
        backoff_base: float = 5.0,
        poll_interval: float = 2.0,
        send_timeout: float = 5.0,
    ) -> None:
        self._repo = repo
        self._sender = sender or self._default_sender
        self._retry_max = max(1, int(retry_max))
        self._backoff_base = max(0.0, float(backoff_base))
        self._poll_interval = max(0.05, float(poll_interval))
        self._send_timeout = send_timeout
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _default_sender(self, url: str, body: dict) -> None:
        send_webhook(url, body, self._send_timeout)

    # ---------------- 生命周期 ----------------

    def start(self) -> None:
        """启动后台轮询线程(daemon);重复调用幂等。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="agentteam-notify-outbox", daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """停轮询并等当前一轮结束;pending 行留在库里等下次启动续发。"""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop_event.wait(self._poll_interval):
            try:
                self.run_once()
            except Exception:
                # 单轮失败绝不拖垮线程;下一轮继续(与 dsh health 同思路:
                # 连续失败在回执里可见,不靠日志告警)
                logger.exception("notify outbox cycle failed")

    # ---------------- 消费逻辑 ----------------

    def run_once(self, now: float | None = None) -> bool:
        """处理一轮到期通知,返回是否有行被处理(测试/健康检查用)。

        遍历全部 pending 行(按 run_id, id 序):
        - 每 run 只处理队头一行:队头失败/退避中,同 run 后续全部等待;
        - 队头退避未到期 → 该 run 本轮跳过(后续行隐式被阻塞);
        - 成功置 sent;失败 attempts+1,达 retry_max 置 dropped,
          否则按 5s 起步指数翻倍(上限 300s)安排下次重试。
        """
        now = time.time() if now is None else now
        rows = self._repo.pending_rows()
        if not rows:
            return False
        acted = False
        head_seen: set[str] = set()
        for row in rows:
            run_id = row["run_id"]
            if run_id in head_seen:
                # 同 run 队头尚未发出,后续消息等待(dsh 串行语义)
                continue
            head_seen.add(run_id)
            if row["next_retry_at"] > now:
                continue
            acted = True
            try:
                body = json.loads(row["payload_json"])
            except (TypeError, ValueError):
                # payload 不可解析属于永久性损坏,直接 dropped 防止堵死该 run
                self._repo.mark_dropped(row["id"], "payload_json unparsable")
                continue
            try:
                self._sender(row["url"], body)
                self._repo.mark_sent(row["id"], now)
            except Exception as exc:
                attempts = int(row["attempts"] or 0) + 1
                error = str(exc)[:_ERROR_SNIPPET]
                if attempts >= self._retry_max:
                    self._repo.mark_dropped(row["id"], error)
                else:
                    delay = min(
                        self._backoff_base * (2 ** (attempts - 1)),
                        _MAX_BACKOFF_SECONDS,
                    )
                    self._repo.mark_retry(row["id"], attempts, now + delay, error)
        return acted
