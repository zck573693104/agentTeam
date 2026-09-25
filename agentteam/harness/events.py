"""opencode SSE 事件 → AgentTeam trace 事件词表映射（SP8）。

职责边界（设计 docs/opencode-harness-design.md §3.3）：
- 引擎自身的确定性事件（worker_start/worker_end/leader_plan/leader_review/
  approval_*）由 engine 直接 emit —— 与 LangGraph 引擎同源同形。
- 本类只负责**会话内**的非确定流：opencode SSE 事件 → tool_call 轨迹、
  token 累计、错误/idle 旗标。SSE 在此仅用于「实时轨迹」与「加速唤醒」；
  run 的正确性状态机（完成/权限/取消）由 engine 轮询兜底，不依赖 SSE。

事件契约（opencode v1.18.32 实测）：
- {id, type:"message.part.updated", properties:{sessionID, part}}
  part.type=="tool" 时 part.tool 为工具名（状态在 part.state.status）。
- {id, type:"message.updated", properties:{sessionID, info:{role,tokens,...}}}
- {id, type:"session.idle", properties:{sessionID}}
- {id, type:"session.error", properties:{sessionID, error:{name,message}}}
"""
from __future__ import annotations

import threading
from typing import Any

from agentteam.runtime.trace import TraceWriter


class EventMapper:
    """每个 run 一个实例；client.subscribe(on_event) 接入全局事件流。"""

    def __init__(self, run_id: str, trace_writer: TraceWriter | None, client) -> None:
        self._run_id = run_id
        self._trace = trace_writer
        self._client = client
        self._lock = threading.Lock()
        # 本 run 跟踪的会话：session_id → agent 名
        self._sessions: dict[str, str] = {}
        # tool part 去重：(session_id, part_id) 已发过 tool_call
        self._seen_tools: set[tuple[str, str]] = set()
        # 会话级 token 累计（message.updated 的最新快照求和）
        self._tokens: dict[str, int] = {}
        # 每条 assistant 消息最近一次 token 快照（增量累计用）
        self._msg_tokens: dict[str, int] = {}
        self._errors: dict[str, str] = {}
        self._idle: set[str] = set()
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

    # ---------- 引擎登记 ----------

    def register_session(self, session_id: str, agent_name: str) -> None:
        with self._lock:
            self._sessions[session_id] = agent_name

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

    def session_idle(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._idle

    # ---------- 事件处理 ----------

    def on_event(self, event: dict[str, Any]) -> None:
        etype = event.get("type", "")
        props = event.get("properties") or {}
        sid = props.get("sessionID")
        if sid is None:
            return
        with self._lock:
            if sid not in self._sessions:
                return  # 非本 run 会话（含控制平面自身的其他 run）

        if etype == "session.idle":
            with self._lock:
                self._idle.add(sid)

        elif etype == "session.error":
            err = props.get("error") or {}
            msg = err.get("message") or err.get("name") or "unknown opencode session error"
            with self._lock:
                self._errors[sid] = str(msg)

        elif etype == "message.updated":
            info = props.get("info") or {}
            usage = info.get("tokens") or {}
            total = int(usage.get("total", 0) or 0)
            if total:
                with self._lock:
                    # 同一 assistant 消息可能多次 updated（增量），取最新快照
                    prev = self._msg_tokens.get(sid + ":" + str(info.get("id")), 0)
                    self._msg_tokens[sid + ":" + str(info.get("id"))] = total
                    self._tokens[sid] = self._tokens.get(sid, 0) - prev + total

        elif etype == "message.part.updated":
            part = props.get("part") or {}
            if part.get("type") != "tool":
                return
            key = (sid, str(part.get("id")))
            tool_name = part.get("tool") or ""
            with self._lock:
                if key in self._seen_tools or not tool_name:
                    return
                self._seen_tools.add(key)
            if self._trace is not None:
                self._trace.emit(
                    self._run_id, "tool_call", self._sessions.get(sid, ""),
                    {"tools": [tool_name]},
                )
