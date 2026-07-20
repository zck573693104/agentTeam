from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from agentteam.storage.base import BaseSqliteRepo
from agentteam.storage.utils import utcnow_iso as _now


class AuditRepo(BaseSqliteRepo):
    """run_events 与 approvals 表的读写，对标 AgentLoop 执行轨迹。

    当与 SqliteSaver 等组件共享同一 sqlite3.Connection 时，须传入同一个
    lock 以串行化所有连接访问（sqlite3.Connection 在多线程下非线程安全，
    即使 check_same_thread=False）。
    """

    def add_event(
        self,
        run_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        tokens: int | None = None,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        chain: str | None = None,
    ) -> int:
        """写入一条 run_event。

        P-B3 全链路 Trace(对标阿里云 AgentTeams "调用链/工具链/决策链"):
        - trace_id:同一次 run 内所有事件共享的根 trace id(空则不写)
        - parent_span_id:父 span id(用于嵌套调用关系,如 tool_call 的父是 worker)
        - chain:事件所属链类型,取值 'call'(调用链)/ 'tool'(工具链)/ 'decision'(决策链)
          None 时根据 event_type 默认推断(_infer_chain)
        """
        if chain is None:
            chain = _infer_chain(event_type)
        cur = self._execute(
            "INSERT INTO run_events "
            "(run_id, event_type, actor, timestamp, payload, duration_ms, tokens, trace_id, parent_span_id, chain) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                event_type,
                actor,
                _now(),
                json.dumps(payload or {}, ensure_ascii=False),
                duration_ms,
                tokens,
                trace_id,
                parent_span_id,
                chain,
            ),
        )
        return cur.lastrowid  # type: ignore[return-value]

    def list_events(
        self, run_id: str, limit: int | None = None, offset: int = 0
    ) -> list[sqlite3.Row]:
        """按 id 升序返回 run 的事件,支持分页。

        limit=None 不分页(向后兼容,但长 run 建议传 limit 避免全量加载);
        limit=N 只返回前 N 条;offset 跳过前 offset 条(配合 limit 翻页)。
        """
        if limit is None:
            return self._fetchall(
                "SELECT * FROM run_events WHERE run_id = ? ORDER BY id ASC",
                (run_id,),
            )
        return self._fetchall(
            "SELECT * FROM run_events WHERE run_id = ? ORDER BY id ASC LIMIT ? OFFSET ?",
            (run_id, limit, offset),
        )

    def list_events_after(self, run_id: str, after_id: int) -> list[sqlite3.Row]:
        """游标增量读取:返回 id > after_id 的事件,按 id ASC 排序。

        用于 SSE 重连只补发新增事件,避免每次重连全表扫描。
        走 (run_id, id) 联合索引 idx_run_events_run_id_id。
        """
        return self._fetchall(
            "SELECT * FROM run_events WHERE run_id = ? AND id > ? ORDER BY id ASC",
            (run_id, after_id),
        )

    def latest_event_id(self, run_id: str) -> int:
        """返回 run 当前最大 event id(无事件返回 0)。

        用于 SSE 初始订阅时获取 last_id 游标,跳过历史已发事件。
        """
        row = self._fetchone(
            "SELECT MAX(id) AS m FROM run_events WHERE run_id = ?", (run_id,)
        )
        return (row["m"] if row and row["m"] is not None else 0)

    def add_approval(self, run_id: str) -> str:
        approval_id = uuid.uuid4().hex
        self._execute(
            "INSERT INTO approvals (id, run_id, status, requested_at) VALUES (?, ?, 'pending', ?)",
            (approval_id, run_id, _now()),
        )
        return approval_id

    def get_approval(self, approval_id: str) -> sqlite3.Row | None:
        return self._fetchone("SELECT * FROM approvals WHERE id = ?", (approval_id,))

    def decide_approval(
        self, approval_id: str, decision: str, decider: str, reason: str | None = None
    ) -> None:
        self._execute(
            "UPDATE approvals SET status = ?, decided_at = ?, decider = ?, reason = ? WHERE id = ?",
            (decision, _now(), decider, reason, approval_id),
        )

    def list_pending_approvals(self, run_id: str) -> list[sqlite3.Row]:
        return self._fetchall(
            "SELECT * FROM approvals WHERE run_id = ? AND status = 'pending'", (run_id,)
        )

    def list_approvals(
        self, run_id: str, limit: int | None = None, offset: int = 0
    ) -> list[sqlite3.Row]:
        """列出某 run 的所有审批记录（含已决策），按请求时间排序,支持分页。"""
        if limit is None:
            return self._fetchall(
                "SELECT * FROM approvals WHERE run_id = ? ORDER BY requested_at",
                (run_id,),
            )
        return self._fetchall(
            "SELECT * FROM approvals WHERE run_id = ? ORDER BY requested_at LIMIT ? OFFSET ?",
            (run_id, limit, offset),
        )

    def list_events_by_chain(
        self, run_id: str, chain: str
    ) -> list[sqlite3.Row]:
        """P-B3:按链类型过滤事件(对标阿里云 AgentTeams 三链检索)。

        chain 取值:
        - 'call':调用链(run_start/run_end/worker_start/worker_end/supervisor)
        - 'tool':工具链(tool_call/approval_requested/approval_decided)
        - 'decision':决策链(leader_plan/leader_review/condition_eval)
        """
        return self._fetchall(
            "SELECT * FROM run_events WHERE run_id = ? AND chain = ? ORDER BY id ASC",
            (run_id, chain),
        )

    def aggregate_by_chain(self) -> dict[str, int]:
        """P-B8: 按 chain 分组计数(三链分布),用于 dashboard 多维统计。

        返回 dict 如 {'call': 120, 'tool': 85, 'decision': 35}。
        chain 列为 NULL 的旧数据归入 'unknown'。
        """
        rows = self._fetchall(
            "SELECT COALESCE(chain, 'unknown') AS chain, COUNT(*) AS n "
            "FROM run_events GROUP BY chain"
        )
        return {row["chain"]: row["n"] for row in rows}

    def aggregate_top_tools(self, limit: int = 10) -> list[dict]:
        """P-B8: 工具调用频次 top N(从 tool_call 事件统计)。

        tool_call 事件的 payload 含 {"tools": ["tool_a", "tool_b"]},
        用 json_extract 拆分数组并统计每个工具的出现次数。

        SQLite json_each 把 JSON 数组拆成行,再 GROUP BY 工具名。
        返回 [{"tool": "search_web", "count": 42}, ...] 按 count 倒序。
        """
        rows = self._fetchall(
            "SELECT t.value AS tool, COUNT(*) AS n "
            "FROM run_events, json_each(payload, '$.tools') AS t "
            "WHERE event_type = 'tool_call' AND t.value IS NOT NULL "
            "GROUP BY t.value ORDER BY n DESC LIMIT ?",
            (limit,),
        )
        return [{"tool": r["tool"], "count": r["n"]} for r in rows]


# P-B3: event_type → chain 推断表
# 调用链(call):run 生命周期 + agent 节点进出
# 工具链(tool):工具调用 + 工具审批
# 决策链(decision):leader plan/review + dag condition 求值
_CHAIN_MAP: dict[str, str] = {
    # call chain
    "run_start": "call",
    "run_end": "call",
    "run_cancelled": "call",
    "worker_start": "call",
    "worker_end": "call",
    "supervisor": "call",
    # tool chain
    "tool_call": "tool",
    "tool_result": "tool",
    "approval_requested": "tool",
    "approval_decided": "tool",
    # decision chain
    "leader_plan": "decision",
    "leader_review": "decision",
    "condition_eval": "decision",
    "plan_rejected": "decision",
}


def _infer_chain(event_type: str) -> str:
    """根据 event_type 推断 chain,未知类型默认 'call'(向后兼容)。"""
    return _CHAIN_MAP.get(event_type, "call")
