"""opencode v2 SSE 事件 → AgentTeam trace 事件词表映射（SP8 / v2 迁移）。

职责边界（设计 docs/opencode-harness-design.md §3.3/§8）：
- 引擎自身的确定性事件（worker_start/worker_end/leader_plan/leader_review/
  approval_*）由 engine 直接 emit —— 与 LangGraph 引擎同源同形。
- 本类只负责**会话内**的非确定流：v2 `session.next.*` 事件 → tool_call 轨迹、
  token 累计、错误旗标，以及交给引擎消费的「观测到的工具调用」（白名单事后拦截）。
  SSE 用于实时轨迹与加速唤醒；run 的正确性状态机由 engine 轮询兜底
  （assistant_done / pending_permissions），不依赖 SSE。

v2 事件契约（opencode v1.18.32 实测；载荷统一在 `data`，不是 v1 的 `properties`）：
- session.next.prompt.admitted / prompted
- session.next.step.started                 {agent, model, snapshot}
- session.next.text.*  /  reasoning.*       文本流 / 思考流
- session.next.tool.input.started           {callID, name}      ← 工具名最先可见
- session.next.tool.input.delta / .ended    {callID, text}      ← 完整入参 JSON
- session.next.tool.called                  {callID, tool, input}
- session.next.tool.success / .failed       {callID, result | error}
- session.next.step.ended                   {finish, cost, tokens, snapshot, files}
  finish=="stop" 即回合结束（"tool-calls" 表示 agent loop 继续）。

GET /api/session/{id}/history 返回同构事件（多一层 durable.seq），故 ingest 路径
同时服务 SSE 与重启重放 —— 服务重启后可用 history 补齐观测视图。
实测 v1.18.32：SSE 帧本身也带 durable.seq（delta 流式事件除外），因此同一条
事件可能从 SSE 和 history 各到一次；处理侧按事件标识去重（id，缺失时退到
durable.seq），重放既不会重复计 token，也不会把已消费的工具调用重新塞回观测队列。
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any

from agentteam.runtime.trace import TraceWriter


@dataclass
class ObservedCall:
    """会话内一次工具调用的观测记录（白名单判定与审计的最小事实）。"""

    call_id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending | success | error
    error: str | None = None

    def args_preview(self, limit: int = 200) -> str:
        try:
            text = json.dumps(self.input, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            text = str(self.input)
        return text if len(text) <= limit else text[:limit] + "…"


def _event_identity(event: dict) -> tuple | None:
    """同一 durable 事件会经 SSE 与 history 各投递一次，用事件自身标识去重。

    不能用 durable.seq 的高水位：跨线程投递可能乱序（实测 fake server 的
    worker 线程与 prompt 线程并发 emit），高水位会把后到的低 seq 事件误判成
    重复而整批丢掉 —— 那等于丢掉工具观测本身。
    """
    eid = event.get("id")
    if eid:
        return ("id", eid)
    dur = event.get("durable")
    if isinstance(dur, dict) and dur.get("seq") is not None:
        try:
            return ("seq", int(dur["seq"]))
        except (TypeError, ValueError):
            return None
    return None


class EventMapper:
    """每个 run 一个实例；client.subscribe(self.on_event) 接入全局事件流。"""

    def __init__(self, run_id: str, trace_writer: TraceWriter | None, client) -> None:
        self._run_id = run_id
        self._trace = trace_writer
        self._client = client
        self._lock = threading.Lock()
        self._sessions: dict[str, str] = {}
        # session_id → {call_id: ObservedCall}
        self._calls: dict[str, dict[str, ObservedCall]] = {}
        # 已发过 tool_call 的 (session_id, call_id)
        self._reported: set[tuple[str, str]] = set()
        # 引擎尚未消费的新调用（事后拦截队列）
        self._unconsumed: dict[str, list[ObservedCall]] = {}
        self._tokens: dict[str, int] = {}
        self._cost: dict[str, float] = {}
        self._finished: set[str] = set()
        self._errors: dict[str, str] = {}
        # 每会话已处理事件的标识集：SSE 实时流与 history 重放是同一条事件的两份
        # 副本，去重后重放既不会重复计 token，也不会把已消费的工具调用重新入队
        self._seen: dict[str, set] = {}
        self._active = False

    # ---------- 生命周期 ----------

    def start(self) -> None:
        """订阅全局事件流（幂等）。"""
        if not self._active:
            self._client.subscribe(self.on_event)
            self._active = True

    def stop(self) -> None:
        if self._active:
            self._client.unsubscribe(self.on_event)
            self._active = False

    # ---------- 引擎登记 / 重放 ----------

    def register_session(self, session_id: str, agent_name: str) -> None:
        with self._lock:
            self._sessions[session_id] = agent_name

    def ingest(self, session_id: str, events: list[dict]) -> None:
        """把 history() 返回的 durable 事件喂进同一处理路径（重启补审计）。"""
        for ev in events or []:
            if isinstance(ev, dict):
                self.on_event(ev)

    # ---------- 引擎查询 ----------

    def session_tokens(self, session_id: str) -> int:
        with self._lock:
            return self._tokens.get(session_id, 0)

    def total_tokens(self) -> int:
        with self._lock:
            return sum(self._tokens.values())

    def session_error(self, session_id: str) -> str | None:
        with self._lock:
            return self._errors.get(session_id)

    def turn_finished(self, session_id: str) -> bool:
        """看到过 step.ended(finish=stop) —— 加速唤醒用，正确性仍由轮询兜底。"""
        with self._lock:
            return session_id in self._finished

    def observed_calls(self, session_id: str) -> list[ObservedCall]:
        with self._lock:
            return list(self._calls.get(session_id, {}).values())

    def take_new_calls(self, session_id: str) -> list[ObservedCall]:
        """取走并清空该会话的未消费新调用（引擎每个轮询周期调用一次）。"""
        with self._lock:
            return self._unconsumed.pop(session_id, [])

    def requeue_calls(self, session_id: str, calls: list[ObservedCall]) -> None:
        """把尚未判定的调用退回队列头部（park 时保留现场，resume 后继续）。"""
        if not calls:
            return
        with self._lock:
            self._unconsumed.setdefault(session_id, [])[:0] = calls

    # ---------- 事件处理 ----------

    def on_event(self, event: dict[str, Any]) -> None:
        etype = event.get("type", "")
        data = event.get("data") or event.get("properties") or {}
        if not isinstance(data, dict):
            return
        sid = data.get("sessionID")
        if sid is None:
            return
        identity = _event_identity(event)
        with self._lock:
            if sid not in self._sessions:
                return  # 非本 run 会话（含其他 run / 控制平面自身）
            if identity is not None:
                seen = self._seen.setdefault(sid, set())
                if identity in seen:
                    return  # 同一条 durable 事件的第二份副本（SSE ↔ history 重放）
                seen.add(identity)

        if etype == "session.next.tool.input.started":
            self._track_call(sid, data.get("callID"), name=data.get("name"))
        elif etype == "session.next.tool.input.ended":
            self._track_call(sid, data.get("callID"), raw_args=data.get("text"))
        elif etype == "session.next.tool.called":
            self._track_call(
                sid, data.get("callID"),
                name=data.get("tool") or data.get("name"),
                input=data.get("input"),
            )
        elif etype == "session.next.tool.success":
            self._settle_call(sid, data.get("callID"), "success")
        elif etype == "session.next.tool.failed":
            err = data.get("error") or data.get("result") or {}
            msg = err.get("message") if isinstance(err, dict) else str(err)
            self._settle_call(sid, data.get("callID"), "error", error=msg or "tool error")
        elif etype == "session.next.step.ended":
            self._account_step(sid, data)
        elif etype == "session.next.step.failed":
            # 失败回合的 SSE 侧形状（实测限流走这条，不发 session.error）
            self._set_error(sid, data.get("error"))
        elif etype == "session.error":
            self._set_error(sid, data.get("error") or data)

    def _set_error(self, sid: str, err: Any) -> None:
        msg = err.get("message") or err.get("name") if isinstance(err, dict) else err
        with self._lock:
            self._errors[sid] = str(msg or "unknown opencode error")

    # ---------- 内部 ----------

    def _track_call(self, sid: str, call_id, name=None, input=None, raw_args=None):
        call_id = str(call_id or "")
        key = (sid, call_id)
        with self._lock:
            bucket = self._calls.setdefault(sid, {})
            call = bucket.get(call_id)
            if call is None:
                call = ObservedCall(call_id=call_id, name=name or "")
                bucket[call_id] = call
                self._unconsumed.setdefault(sid, []).append(call)
            if name and not call.name:
                call.name = name
            if isinstance(input, dict) and input:
                call.input = input
            elif isinstance(raw_args, str) and raw_args.strip() and not call.input:
                try:
                    parsed = json.loads(raw_args)
                    call.input = parsed if isinstance(parsed, dict) else {"args": parsed}
                except ValueError:
                    call.input = {"args": raw_args}
            known = key in self._reported
        if not known and call.name:
            self._report(sid, call)

    def _settle_call(self, sid: str, call_id, status: str, error: str | None = None):
        with self._lock:
            call = self._calls.get(sid, {}).get(str(call_id or ""))
            if call is None:
                return
            call.status = status
            if error:
                call.error = error

    def _account_step(self, sid: str, data: dict) -> None:
        tokens = data.get("tokens") or {}
        delta = 0
        for key in ("input", "output", "reasoning"):
            delta += int(tokens.get(key, 0) or 0)
        cost = data.get("cost")
        with self._lock:
            if delta:
                self._tokens[sid] = self._tokens.get(sid, 0) + delta
            if cost:
                self._cost[sid] = self._cost.get(sid, 0.0) + float(cost)
            if (data.get("finish") or "") == "stop":
                self._finished.add(sid)

    def _report(self, sid: str, call: ObservedCall) -> None:
        with self._lock:
            key = (sid, call.call_id)
            if key in self._reported:
                return
            self._reported.add(key)
        if self._trace is not None:
            self._trace.emit(
                self._run_id, "tool_call", self._sessions.get(sid, ""),
                {"tools": [call.name]},
            )
