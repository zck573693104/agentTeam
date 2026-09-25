"""Fake opencode server：按 v1.18.32 契约实现 harness 引擎所需的子集。

目的：确定性、无外部依赖地测试 HarnessRunner 全部编排/审批语义。
契约依据：docs/opencode-harness-design.md §2（真实 server 实测）。

实现的端点（路径与 payload 形状对齐真实 server）：
- POST   /session                          建会话（permission 规则集透传存储）
- GET    /session/{id}                     会话信息（tokens 聚合）
- POST   /session/{id}/message             同步 prompt（plan/review 用）
- POST   /session/{id}/prompt_async        异步 prompt（worker 用，busy→idle）
- GET    /session/{id}/message             消息列表 [{info, parts}]
- GET    /session/status                   忙碌会话 map
- GET    /permission                       pending permission 列表
- POST   /session/{id}/permissions/{pid}   审批回帖（once/always/reject）
- POST   /session/{id}/abort               中止会话（emit idle, finish=abort）
- GET    /event                            SSE 事件流
- PATCH  /config / POST /mcp / GET /mcp   配置/MCP（记录 + 幂等）

剧本（Script）：测试用例注入 plan JSON 与 worker 行为序列：
- worker step: {"type": "tool", "name": "write_file", "ask": bool, "result": str}
               {"type": "final", "text": str}
- ask=True 的 tool 会挂起 pending permission，直到测试回帖。
"""
from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class Script:
    """可编程剧本：plan 回复 + 按 session title（worker 名）的步骤序列。"""

    def __init__(self) -> None:
        self.plan: dict[str, Any] = {"steps": [], "execution_mode": "sequential"}
        self.review_text = "LGTM"
        # worker 名 → 步骤列表（每轮 dispatch 消费一份；列表耗尽后复读最后一份）
        self.worker_steps: dict[str, list[dict]] = {}
        # plan/review 之外的通用文本回复（按 title 前缀匹配 leader 会话）
        self.leader_plan_error: str | None = None
        self._step_pos: dict[str, int] = {}
        self._lock = threading.Lock()

    def next_steps(self, worker_name: str) -> list[dict]:
        """返回该 worker 本次 dispatch 的步骤序列。

        worker_steps[name] 是「每次 dispatch 的序列」列表：
        [seq0, seq1, ...]；每次调用消耗下一个，耗尽后复读最后一个。
        单序列可直接写成 step dict 列表（最常用）。
        """
        with self._lock:
            pos = self._step_pos.get(worker_name, 0)
            entries = self.worker_steps.get(worker_name, [])
            if not entries:
                return [{"type": "final", "text": f"{worker_name} done"}]
            self._step_pos[worker_name] = min(pos + 1, len(entries) - 1)
            seq = entries[min(pos, len(entries) - 1)]
        if isinstance(seq, dict):
            seq = [seq]
        return list(seq)


class _Session:
    def __init__(self, sid: str, body: dict) -> None:
        self.id = sid
        self.title = body.get("title") or ""
        self.model = body.get("model")
        self.permission = body.get("permission") or []
        self.messages: list[dict] = []
        self.busy = False
        self.aborted = False
        self.tokens = {"input": 0, "output": 0, "reasoning": 0}
        self.pending_permission: dict | None = None
        self.reply_gate: threading.Event = threading.Event()
        self.reply_value: str | None = None
        self.on_reply: Any = None  # callback(reply)


class FakeOpenCodeServer:
    """进程内 fake server。start() 后用 base_url 连接。"""

    def __init__(self, script: Script) -> None:
        self.script = script
        self.sessions: dict[str, _Session] = {}
        self.config: dict = {}
        self.mcps: dict[str, Any] = {}
        self._events: list[dict] = []
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._counter = 0
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.requests_log: list[tuple[str, str]] = []

    # ---------- 生命周期 ----------

    def start(self) -> str:
        server = ThreadingHTTPServer(("127.0.0.1", 0), self._make_handler())
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        return f"http://127.0.0.1:{server.server_address[1]}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    # ---------- 事件 ----------

    def emit(self, etype: str, props: dict) -> None:
        with self._lock:
            self._counter += 1
            event = {"id": f"evt_{self._counter}", "type": etype, "properties": props}
            self._events.append(event)
            subs = list(self._subscribers)
        for q in subs:
            q.put(event)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    # ---------- 内部 ----------

    def _worker_name(self, session: _Session) -> str:
        # title 形如 "{team}:{worker}"
        return session.title.split(":", 1)[-1] if ":" in session.title else session.title

    def _add_message(self, sess: _Session, role: str, text: str, finish: str | None,
                     tool_parts: list[dict] | None = None) -> dict:
        msg_id = f"msg_{uuid.uuid4().hex[:16]}"
        parts: list[dict] = []
        for tp in tool_parts or []:
            parts.append({
                "id": f"prt_{uuid.uuid4().hex[:12]}", "type": "tool",
                "tool": tp["tool"], "sessionID": sess.id, "messageID": msg_id,
                "state": {"status": "completed", "output": tp.get("result", "")},
            })
        if text:
            parts.append({
                "id": f"prt_{uuid.uuid4().hex[:12]}", "type": "text",
                "text": text, "sessionID": sess.id, "messageID": msg_id,
            })
        info = {
            "id": msg_id, "role": role, "sessionID": sess.id,
            "tokens": {"total": 42, "input": 30, "output": 10, "reasoning": 2},
            "finish": finish,
        }
        msg = {"info": info, "parts": parts}
        sess.messages.append(msg)
        sess.tokens["input"] += 30
        sess.tokens["output"] += 10
        sess.tokens["reasoning"] += 2
        self.emit("message.updated", {"sessionID": sess.id, "info": info})
        for p in parts:
            self.emit("message.part.updated", {"sessionID": sess.id, "part": p, "time": time.time()})
        return msg

    def _run_worker_script(self, sess: _Session) -> None:
        """异步执行 worker 剧本：tool 步（可挂权限）→ final 步。"""
        worker = self._worker_name(sess)
        steps = self.script.next_steps(worker)
        self._execute_steps(sess, list(steps))

    def _execute_steps(self, sess: _Session, steps: list[dict]) -> None:
        for step in steps:
            if sess.aborted:
                return
            if step.get("type") == "tool":
                tool = step["name"]
                if step.get("ask"):
                    # 挂起 pending permission，等回帖后继续剩余 steps
                    perm_id = f"per_{uuid.uuid4().hex[:12]}"
                    perm = {
                        "id": perm_id, "sessionID": sess.id,
                        "permission": tool, "patterns": ["*"],
                        "metadata": {}, "always": [],
                    }
                    sess.pending_permission = perm
                    sess.busy = True
                    self.emit("permission.asked", {
                        "sessionID": sess.id, "requestID": perm_id,
                        "type": tool, "pattern": "*",
                    })
                    self._on_permission_reply_then(sess, step, steps)
                    return
                self.emit_tool_done(sess, tool, step.get("result", "ok"))
            elif step.get("type") == "sleep":
                # 测试取消：保持 busy 一段时间（可被 abort 打断）
                deadline = time.monotonic() + float(step.get("seconds", 3))
                while time.monotonic() < deadline and not sess.aborted:
                    time.sleep(0.02)
                if not sess.aborted:
                    sess.busy = False
                    self._add_message(sess, "assistant", "slept", finish="stop")
                    self.emit("session.idle", {"sessionID": sess.id})
                return
            elif step.get("type") == "final":
                sess.busy = False
                self._add_message(sess, "assistant", step.get("text", ""), finish="stop")
                self.emit("session.idle", {"sessionID": sess.id})
                return
        # 无 final 步：兜底收尾
        sess.busy = False
        self._add_message(sess, "assistant", "done", finish="stop")
        self.emit("session.idle", {"sessionID": sess.id})

    def emit_tool_done(self, sess: _Session, tool: str, result: str) -> None:
        self._add_message(sess, "tool", "", finish=None,
                          tool_parts=[{"tool": tool, "result": result}])

    def _on_permission_reply_then(self, sess: _Session, granted_step: dict,
                                  rest: list[dict]) -> None:
        """回帖到达后：once → 继续执行；reject → 记拒绝并出 final。"""

        def resume() -> None:
            reply = sess.reply_value
            sess.pending_permission = None
            if reply == "reject":
                sess.busy = False
                self._add_message(sess, "tool", "", finish=None, tool_parts=[
                    {"tool": granted_step["name"], "result": "工具调用已被拒绝"}
                ])
                self._add_message(sess, "assistant", "已跳过被拒工具，直接完成。", finish="stop")
                self.emit("session.idle", {"sessionID": sess.id})
                return
            self.emit_tool_done(sess, granted_step["name"],
                                granted_step.get("result", "ok"))
            idx = rest.index(granted_step)
            self._execute_steps(sess, rest[idx + 1:])

        def wait_and_resume() -> None:
            sess.reply_gate.wait(timeout=30)
            resume()

        threading.Thread(target=wait_and_resume, daemon=True).start()

    # ---------- HTTP handler ----------

    def _make_handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # 静默
                pass

            def _send(self, code: int, payload: Any, content_type: str = "application/json"):
                body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self) -> dict:
                length = int(self.headers.get("Content-Length") or 0)
                if not length:
                    return {}
                return json.loads(self.rfile.read(length) or b"{}")

            def do_GET(self):
                outer.requests_log.append(("GET", self.path))
                if self.path == "/global/health":
                    self._send(200, {"healthy": True}); return
                if self.path == "/event":
                    self._serve_sse(); return
                if self.path == "/permission":
                    with outer._lock:
                        pending = [
                            s.pending_permission for s in outer.sessions.values()
                            if s.pending_permission
                        ]
                    self._send(200, pending); return
                if self.path == "/session/status":
                    with outer._lock:
                        busy = {
                            s.id: {"isBusy": True}
                            for s in outer.sessions.values() if s.busy
                        }
                    self._send(200, busy); return
                if self.path == "/mcp":
                    self._send(200, outer.mcps); return
                if self.path.startswith("/session/"):
                    parts = self.path.strip("/").split("/")
                    sess = outer.sessions.get(parts[1])
                    if sess is None:
                        self._send(404, {"error": "session not found"}); return
                    if len(parts) == 2:
                        self._send(200, {
                            "id": sess.id, "title": sess.title,
                            "tokens": dict(sess.tokens), "cost": 0.0,
                        }); return
                    if len(parts) >= 3 and parts[2] == "message":
                        self._send(200, list(sess.messages)); return
                self._send(404, {"error": f"no route: {self.path}"})

            def do_PATCH(self):
                outer.requests_log.append(("PATCH", self.path))
                body = self._body()
                if self.path == "/config":
                    for k, v in body.items():
                        if k in outer.config and isinstance(outer.config[k], dict) and isinstance(v, dict):
                            outer.config[k].update(v)
                        else:
                            outer.config[k] = v
                    self._send(200, outer.config); return
                self._send(404, {"error": "no route"})

            def do_POST(self):
                outer.requests_log.append(("POST", self.path))
                body = self._body()
                parts = self.path.strip("/").split("/")
                if self.path == "/session":
                    outer._counter += 1
                    sid = f"ses_{uuid.uuid4().hex[:16]}"
                    sess = _Session(sid, body)
                    with outer._lock:
                        outer.sessions[sid] = sess
                    outer.emit("session.created", {"sessionID": sid, "title": sess.title})
                    self._send(200, {
                        "id": sid, "title": sess.title,
                        "tokens": dict(sess.tokens), "cost": 0.0,
                    }); return
                if self.path == "/mcp":
                    outer.mcps[body["name"]] = body["config"]
                    self._send(200, outer.mcps); return
                if len(parts) >= 3 and parts[0] == "session":
                    sess = outer.sessions.get(parts[1])
                    if sess is None:
                        self._send(404, {"error": "session not found"}); return
                    action = parts[2]
                    if action == "message":
                        # 同步 prompt：plan/review（立即处理）
                        text = "".join(p.get("text", "") for p in body.get("parts", []))
                        outer._handle_sync_prompt(sess, body, text)
                        self._send(200, list(sess.messages)[-1]); return
                    if action == "prompt_async":
                        text = "".join(p.get("text", "") for p in body.get("parts", []))
                        sess.busy = True
                        self._send(200, {"data": {"sessionID": sess.id, "text": text}})
                        threading.Thread(
                            target=outer._run_worker_script, args=(sess,), daemon=True
                        ).start()
                        return
                    if action == "abort":
                        sess.aborted = True
                        sess.reply_gate.set()
                        sess.busy = False
                        if sess.pending_permission:
                            sess.pending_permission = None
                        outer.emit("session.idle", {"sessionID": sess.id})
                        self._send(200, True); return
                    if action == "permissions" and len(parts) >= 4:
                        perm_id = parts[3]
                        reply = body.get("response")
                        if sess.pending_permission and sess.pending_permission["id"] == perm_id:
                            sess.reply_value = reply
                            sess.reply_gate.set()
                        outer.emit("permission.replied", {
                            "sessionID": sess.id, "requestID": perm_id, "reply": reply,
                        })
                        self._send(200, True); return
                self._send(404, {"error": f"no route: {self.path}"})

            def _serve_sse(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                q = outer.subscribe()
                try:
                    while True:
                        try:
                            event = q.get(timeout=1)
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                            continue
                        data = json.dumps(event).encode()
                        self.wfile.write(b"data: " + data + b"\n\n")
                        self.wfile.flush()
                except Exception:
                    pass
                finally:
                    with outer._lock:
                        if q in outer._subscribers:
                            outer._subscribers.remove(q)

        return Handler

    # ---------- 同步 prompt（plan/review） ----------

    def _handle_sync_prompt(self, sess: _Session, body: dict, text: str) -> None:
        fmt = body.get("format")
        if fmt and fmt.get("type") == "json_schema":
            # 结构化输出：返回剧本 plan
            if self.script.leader_plan_error:
                self._add_message(sess, "assistant", self.script.leader_plan_error, finish="stop")
                self.emit("session.error", {
                    "sessionID": sess.id,
                    "error": {"name": "StructuredOutputError", "message": "bad plan"},
                })
                return
            self._add_message(sess, "assistant",
                              json.dumps(self.script.plan, ensure_ascii=False), finish="stop")
            return
        # review / 普通文本
        self._add_message(sess, "assistant", self.script.review_text, finish="stop")
