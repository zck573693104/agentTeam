"""WebhookDeliveryRepo:webhook 投递记录的 SQLite 持久化。

Graph Engineering P4 外部 receipt 幂等:
文章原文:"付款、部署、发消息遇到超时,应先读取外部 receipt,再决定重试或补偿"。

实现策略:
- 每次投递生成唯一 delivery_id,作为 payload 的一部分发给接收方
- 接收方可基于 delivery_id 去重(收到重复 delivery_id 不重复处理)
- 投递失败时用同一 delivery_id 重试,接收方据此识别重试
- 投递状态流转:pending → delivered(成功) / failed(重试耗尽)

线程安全:与其他 Repo 共享同一 sqlite3.Connection 时,
须传入同一把 threading.Lock 串行化所有访问。
"""
from __future__ import annotations

import sqlite3
from typing import Any

from agentteam.storage.base import BaseSqliteRepo
from agentteam.storage.utils import utcnow_iso as _now


class WebhookDeliveryRepo(BaseSqliteRepo):
    """webhook_deliveries 表的 CRUD 仓库。"""

    def create_delivery(
        self,
        delivery_id: str,
        run_id: str,
        event_type: str,
        target_url: str,
        payload: dict[str, Any],
    ) -> None:
        """创建一条 pending 投递记录(首次投递时调用)。"""
        import json
        self._execute(
            "INSERT INTO webhook_deliveries "
            "(delivery_id, run_id, event_type, target_url, payload, status, "
            " attempts, first_attempt_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', 0, ?)",
            (
                delivery_id, run_id, event_type, target_url,
                json.dumps(payload, ensure_ascii=False), _now(),
            ),
        )

    def mark_attempt(
        self,
        delivery_id: str,
        success: bool,
        error: str | None = None,
    ) -> None:
        """记录一次投递尝试结果,自增 attempts。

        success=True 时置 status='delivered' + delivered_at;
        success=False 时保留 status='pending'(由 mark_failed 在重试耗尽后置 failed)。
        """
        now = _now()
        if success:
            self._execute(
                "UPDATE webhook_deliveries SET "
                "  attempts = attempts + 1, last_attempt_at = ?, "
                "  status = 'delivered', delivered_at = ?, last_error = NULL "
                "WHERE delivery_id = ?",
                (now, now, delivery_id),
            )
        else:
            self._execute(
                "UPDATE webhook_deliveries SET "
                "  attempts = attempts + 1, last_attempt_at = ?, last_error = ? "
                "WHERE delivery_id = ?",
                (now, error, delivery_id),
            )

    def mark_failed(self, delivery_id: str, error: str | None = None) -> None:
        """重试耗尽后置 status='failed'(保留 attempts 与 last_error 历史)。"""
        self._execute(
            "UPDATE webhook_deliveries SET status = 'failed', last_error = ? "
            "WHERE delivery_id = ?",
            (error, delivery_id),
        )

    def get_delivery(self, delivery_id: str) -> sqlite3.Row | None:
        """查询单条投递记录(用于 receipt 检查:已 delivered 则不重试)。"""
        return self._fetchone(
            "SELECT * FROM webhook_deliveries WHERE delivery_id = ?",
            (delivery_id,),
        )

    def list_pending(self, run_id: str | None = None) -> list[dict]:
        """列出 pending 投递(供补偿任务扫描重试)。

        run_id=None 列全部 pending;指定 run_id 只列该 run 的。
        """
        if run_id is None:
            rows = self._fetchall(
                "SELECT * FROM webhook_deliveries WHERE status = 'pending' "
                "ORDER BY first_attempt_at ASC"
            )
        else:
            rows = self._fetchall(
                "SELECT * FROM webhook_deliveries "
                "WHERE status = 'pending' AND run_id = ? "
                "ORDER BY first_attempt_at ASC",
                (run_id,),
            )
        return [dict(r) for r in rows]

    def list_deliveries(self, run_id: str) -> list[dict]:
        """列出某 run 的所有投递记录(按时间倒序)。"""
        rows = self._fetchall(
            "SELECT * FROM webhook_deliveries WHERE run_id = ? "
            "ORDER BY first_attempt_at DESC, delivery_id DESC",
            (run_id,),
        )
        return [dict(r) for r in rows]
