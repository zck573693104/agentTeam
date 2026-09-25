"""Fake opencode server：按 v2 (/api/*) 契约实现 harness 引擎所需的子集。

目的：确定性、无外部依赖地测试 HarnessRunner 全部编排/审批语义。
契约依据：docs/opencode-harness-design.md §2/§8（真实 server 实测）。

实现的端点（响应统一包 {"data": ...}，与真实 v2 一致）：
- POST /api/session                       建会话（agent 名作标签，无 title/permission）
- GET  /api/session/{id}                  会话信息（tokens 聚合恒 0，与真实一致）
- POST /api/session/{id}/prompt           异步 prompt（v2 唯一形态；
                                          **resume=false 只入队不调度回合**，
                                          与 1.18.32 实测一致）
- GET  /api/session/{id}/message          消息列表，**倒序**（真实契约如此）
- GET  /api/session/{id}/history          durable 事件（含 durable.seq）
- POST /api/session/{id}/interrupt        中断在飞回合
- GET  /api/permission/request            pending permission（v2 形态 {action,resources}）
- POST /api/session/{id}/permission/{rid}/reply
- GET  /api/event                         SSE（事件载荷在 data，类型 session.next.*）
- GET  /api/health
- PATCH /config / POST /mcp / GET /mcp    v1 配置面（ensure_backend 用，无 v2 等价）

剧本（Script）：测试注入 plan 与按 worker（= 会话 agent 标签）的行为序列：
- {"type":"tool","name":"write","args":{...},"result":str,"settle":0.2}
  先 emit tool.input.started/ended + tool.called，pause `settle` 秒给引擎观测/中断的
  窗口，再 emit tool.success。**引擎 interrupt 到达时剩余步骤存回会话**，
  下一次 prompt（审批放行后的续跑指令）从中断处继续 —— 复现真实的事后中断时序。
- {"type":"final","text":str}   回合结束（finish=stop）
- {"type":"sleep","seconds":n}  长时间忙碌（测取消）
"""
from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs

DEFAULT_SETTLE = 0.2


class Script:
    """可编程剧本：plan 回复 + 按 worker 名（会话 agent 标签）的步骤序列。"""

    def __init__(self) -> None:
        self.plan: dict[str, Any] = {"steps": [], "execution_mode": "sequential"}
        # 非 None 时原样作为 plan 回答（测「计划不是 JSON」路径）
        self.plan_raw: str | None = None
        self.review_text = "LGTM"
        # worker 名 → 每次 dispatch 的步骤序列（列表的列表；耗尽后复读最后一份）
        self.worker_steps: dict[str, list[dict]] = {}
        # 非 None 时该会话上报 session.error（测错误传播）
        self.session_error: str | None = None
        # agent 名 → 错误文本：该会话的回合以 finish=="error" +
        # session.next.step.failed 终止（实测上游限流的形状）
        self.turn_errors: dict[str, str] = {}
        # True 时事件只进 history、不推 SSE：复现「REST 完成信号领先 SSE 观测」
        self.blackhole = False
        # >0 时接下来这么多次 leader 回合「干净结束但一个 text part 都没有」
        # （实测免费模型偶发：step.ended finish=stop、output=0、content=[]）
        self.empty_turns = 0
        # True 时任何 prompt 都只入队不调度（真实 server 上游限流后的形态）
        self.never_schedules = False
        self._step_pos: dict[str, int] = {}
        self._lock = threading.Lock()

    def take_empty_turn(self) -> bool:
        with self._lock:
            if self.empty_turns > 0:
                self.empty_turns -= 1
                return True
            return False

    def next_steps(self, worker_name: str) -> list[dict]:
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
        self.agent = body.get("agent") or ""
        self.model = body.get("model") or {}
        self.messages: list[dict] = []
        self.events: list[dict] = []
        self.busy = False
        self.interrupted = False
        self.admitted_only = False  # resume=False 的输入：入队但永不调度
        self.remaining: list[dict] = []
        self.pending_permission: dict | None = None
        self.reply_gate = threading.Event()
        self.reply_value: str | None = None
        self.lock = threading.Lock()


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

    def emit(self, etype: str, data: dict) -> None:
        with self._lock:
            self._counter += 1
            seq = self._counter
            event = {
                "id": f"evt_{seq}",
                "type": etype,
                "durable": {"seq": seq},
                "data": data,
            }
            self._events.append(event)
            subs = [] if self.script.blackhole else list(self._subscribers)
            sess = self.sessions.get(data.get("sessionID") or "")
        if sess is not None:
            sess.events.append(event)
        for q in subs:
            q.put(event)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    # ---------- 消息 / 回合执行 ----------

    def _add_message(self, sess: _Session, *, msg_type: str, finish: str | None,
                     content: list[dict], tokens: dict | None = None,
                     error: dict | None = None) -> dict:
        msg = {
            "id": f"msg_{uuid.uuid4().hex[:16]}",
            "type": msg_type,
            "agent": sess.agent,
            "model": sess.model,
            "finish": finish,
            "content": content,
            "tokens": tokens or {"input": 0, "output": 0, "reasoning": 0},
            "snapshot": {"start": "snap0", "end": "snap1", "files": []},
            "time": {"created": int(time.time() * 1000)},
        }
        if error is not None:
            msg["error"] = error
        with sess.lock:
            sess.messages.append(msg)
        return msg

    def _fail_turn(self, sess: _Session, message: str) -> None:
        """真实 v2 上游限流的形状（1.18.32 实测）：

        assistant 消息 finish=="error" + error.message，配套事件是
        `session.next.step.failed`（**不是** session.error），没有 step.ended。
        """
        err = {"type": "unknown", "message": message}
        self.emit("session.next.step.started",
                  {"sessionID": sess.id, "agent": sess.agent})
        msg = self._add_message(sess, msg_type="assistant", finish="error",
                                content=[], error=err)
        self.emit("session.next.step.failed",
                  {"sessionID": sess.id,
                   "assistantMessageID": msg["id"], "error": err})
        sess.busy = False

    def _execute(self, sess: _Session, steps: list[dict]) -> None:
        for i, step in enumerate(steps):
            if sess.interrupted:
                sess.remaining = steps[i:]
                return
            kind = step.get("type")
            if kind == "tool":
                self._run_tool_step(sess, step, steps, i)
                return
            if kind == "sleep":
                deadline = time.monotonic() + float(step.get("seconds", 3))
                while time.monotonic() < deadline and not sess.interrupted:
                    time.sleep(0.02)
                if sess.interrupted:
                    return
                self._finish_turn(sess, "slept")
                return
            if kind == "final":
                self._finish_turn(sess, step.get("text", ""))
                return
        self._finish_turn(sess, "done")

    def _run_tool_step(self, sess: _Session, step: dict,
                       steps: list[dict], index: int) -> None:
        call_id = f"call_{uuid.uuid4().hex[:12]}"
        name = step["name"]
        args = step.get("args") or {}
        sid = sess.id
        self.emit("session.next.step.started",
                  {"sessionID": sid, "agent": sess.agent})
        self.emit("session.next.tool.input.started",
                  {"sessionID": sid, "callID": call_id, "name": name})
        self.emit("session.next.tool.input.ended",
                  {"sessionID": sid, "callID": call_id,
                   "text": json.dumps(args, ensure_ascii=False)})
        self.emit("session.next.tool.called",
                  {"sessionID": sid, "callID": call_id, "tool": name, "input": args})
        # 给引擎观测 + POST interrupt 的窗口（真实世界这里工具已在执行）
        deadline = time.monotonic() + float(step.get("settle", DEFAULT_SETTLE))
        while time.monotonic() < deadline and not sess.interrupted:
            time.sleep(0.01)
        if sess.interrupted:
            sess.remaining = steps[index:]
            return
        self.emit("session.next.tool.success",
                  {"sessionID": sid, "callID": call_id, "tool": name,
                   "result": {"type": "text", "value": step.get("result", "ok")}})
        self._add_message(sess, msg_type="assistant", finish="tool-calls",
                          content=[{"type": "tool", "id": call_id, "name": name,
                                     "state": {"status": "completed", "input": args,
                                               "content": []}}])
        self.emit("session.next.step.ended",
                  {"sessionID": sid, "finish": "tool-calls",
                   "tokens": {"input": 0, "output": 0, "reasoning": 0},
                   "cost": 0, "snapshot": "snap1", "files": []})
        self._execute(sess, steps[index + 1:])

    def _finish_turn(self, sess: _Session, text: str, *,
                     blank: bool = False) -> None:
        self._add_message(
            sess, msg_type="assistant", finish="stop",
            content=[] if blank else [
                {"type": "text", "id": f"prt_{uuid.uuid4().hex[:8]}", "text": text}],
            tokens={"input": 30, "output": 0 if blank else 10, "reasoning": 2},
        )
        sess.busy = False
        self.emit("session.next.step.ended",
                  {"sessionID": sess.id, "finish": "stop",
                   "tokens": {"input": 30, "output": 10, "reasoning": 2},
                   "cost": 0, "snapshot": "snap1", "files": []})

    def _run_script(self, sess: _Session, text: str) -> None:
        try:
            if self.script.session_error:
                self.emit("session.error", {
                    "sessionID": sess.id,
                    "error": {"name": "ProviderError",
                              "message": self.script.session_error},
                })
                sess.busy = False
                return
            if sess.agent in self.script.turn_errors:
                self._fail_turn(sess, self.script.turn_errors[sess.agent])
                return
            if sess.agent in self.script.worker_steps:
                steps = sess.remaining or self.script.next_steps(sess.agent)
                sess.remaining = []
                self._execute(sess, steps)
                return
            self._run_leader(sess, text)
        except Exception as e:  # pragma: no cover - 剧本写错时给出口
            self.emit("session.error", {
                "sessionID": sess.id,
                "error": {"name": "FakeScriptError", "message": str(e)},
            })
            sess.busy = False

    def _run_leader(self, sess: _Session, text: str) -> None:
        """plan / review 一次性会话：按 prompt 文本分流（v2 无 per-request format）。"""
        if self.script.take_empty_turn():
            # 真实模型偶发的「空回合」：finish=stop 但 content 里没有任何 text part
            self._finish_turn(sess, "", blank=True)
            return
        if "步骤计划" in text:
            answer = (self.script.plan_raw if self.script.plan_raw is not None
                      else json.dumps(self.script.plan, ensure_ascii=False))
        else:
            answer = self.script.review_text
        self._finish_turn(sess, answer)

    # ---------- HTTP handler ----------

    def _make_handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # 静默
                pass

            def _send(self, code: int, payload: Any,
                      content_type: str = "application/json"):
                body = payload if isinstance(payload, bytes) else json.dumps(
                    payload, ensure_ascii=False).encode()
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
                path = self.path.split("?")[0]
                if path == "/api/health":
                    self._send(200, {"data": {"healthy": True}})
                    return
                if path == "/api/event":
                    self._serve_sse()
                    return
                if path == "/api/permission/request":
                    with outer._lock:
                        pending = [s.pending_permission for s in outer.sessions.values()
                                   if s.pending_permission]
                    self._send(200, {"data": pending})
                    return
                if path == "/mcp":
                    self._send(200, outer.mcps)
                    return
                seg = path.strip("/").split("/")
                # /api/session/{id} | /api/session/{id}/message | /api/session/{id}/history
                if len(seg) >= 3 and seg[0] == "api" and seg[1] == "session":
                    sess = outer.sessions.get(seg[2])
                    if sess is None:
                        self._send(404, {"error": "session not found"})
                        return
                    sub = seg[3] if len(seg) >= 4 else ""
                    if sub == "":
                        self._send(200, {"data": {
                            "id": sess.id, "agent": sess.agent,
                            "tokens": {"input": 0, "output": 0, "reasoning": 0},
                            "cost": 0.0}})
                        return
                    if sub == "message":
                        with sess.lock:
                            msgs = list(sess.messages)
                        self._send(200, {"data": list(reversed(msgs))})
                        return
                    if sub == "history":
                        q = parse_qs(self.path.split("?")[1] if "?" in self.path else "")
                        limit = int((q.get("limit") or ["100"])[0])
                        if limit > 100:  # 真实 v2 上限：超限 400 InvalidRequestError
                            self._send(400, {
                                "_tag": "InvalidRequestError",
                                "message": f"Expected a value less than or equal "
                                           f"to 100, got {limit}", "kind": "Query"})
                            return
                        self._send(200, {"data": list(sess.events)})
                        return
                self._send(404, {"error": f"no route: {self.path}"})

            def do_POST(self):
                outer.requests_log.append(("POST", self.path))
                body = self._body()
                path = self.path.split("?")[0]
                seg = path.strip("/").split("/")
                if path == "/api/session":
                    self._send(200, {"data": outer._create_session(body)})
                    return
                if path == "/mcp":
                    outer.mcps[body["name"]] = body["config"]
                    self._send(200, {"data": outer.mcps})
                    return
                if len(seg) >= 4 and seg[0] == "api" and seg[1] == "session":
                    sess = outer.sessions.get(seg[2])
                    if sess is None:
                        self._send(404, {"error": "session not found"})
                        return
                    action = seg[3]
                    if action == "prompt":
                        self._handle_prompt(sess, body)
                        return
                    if action == "interrupt":
                        sess.interrupted = True
                        sess.reply_gate.set()
                        sess.busy = False
                        self._send(200, {"data": True})
                        return
                    if action == "permission" and len(seg) >= 6:
                        reply = body.get("reply")
                        if (sess.pending_permission
                                and sess.pending_permission["id"] == seg[4]):
                            sess.reply_value = reply
                            sess.reply_gate.set()
                            sess.pending_permission = None
                        self._send(200, {"data": {"effect": "allowed"}})
                        return
                self._send(404, {"error": f"no route: {self.path}"})

            def do_PATCH(self):
                outer.requests_log.append(("PATCH", self.path))
                if self.path == "/config":
                    for k, v in self._body().items():
                        cur = outer.config.get(k)
                        if isinstance(cur, dict) and isinstance(v, dict):
                            cur.update(v)
                        else:
                            outer.config[k] = v
                    self._send(200, {"data": outer.config})
                    return
                self._send(404, {"error": "no route"})

            def _handle_prompt(self, sess: _Session, body: dict) -> None:
                text = ((body.get("prompt") or {}).get("text")) or ""
                user_msg = {
                    "id": f"msg_{uuid.uuid4().hex[:16]}", "type": "user",
                    "agent": sess.agent, "finish": None,
                    "content": [{"type": "text", "id": f"prt_{uuid.uuid4().hex[:8]}",
                                 "text": text}],
                    "tokens": None, "snapshot": None,
                    "time": {"created": int(time.time() * 1000)},
                }
                with sess.lock:
                    sess.messages.append(user_msg)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                payload = json.dumps({"data": {
                    "id": user_msg["id"], "sessionID": sess.id,
                    "admittedSeq": len(sess.messages)}})
                body_bytes = payload.encode()
                self.send_header("Content-Length", str(len(body_bytes)))
                self.end_headers()
                self.wfile.write(body_bytes)
                if body.get("resume") is False or outer.script.never_schedules:
                    # 真实 v2 语义（1.18.32 实测 + spec「schedule agent-loop
                    # execution unless resume is false」）：只 durable 入队，
                    # 回合永不被调度，且没有任何报错信号。上游限流后也会
                    # 呈现同一形态（never_schedules 剧本位）。
                    sess.admitted_only = True
                    return
                # 新一轮 prompt = 引擎放行后的续跑：清除中断标记，接着 remaining 跑
                sess.interrupted = False
                sess.busy = True
                threading.Thread(target=outer._run_script,
                                 args=(sess, text), daemon=True).start()

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
                        self.wfile.write(
                            b"data: " + json.dumps(event).encode() + b"\n\n")
                        self.wfile.flush()
                except Exception:
                    pass
                finally:
                    with outer._lock:
                        if q in outer._subscribers:
                            outer._subscribers.remove(q)

        return Handler

    # ---------- 会话与权限辅助 ----------

    def _create_session(self, body: dict) -> dict:
        with self._lock:
            self._counter += 1
        sid = f"ses_{uuid.uuid4().hex[:16]}"
        sess = _Session(sid, body)
        with self._lock:
            self.sessions[sid] = sess
        return {"id": sid, "agent": sess.agent, "model": sess.model,
                "tokens": {"input": 0, "output": 0, "reasoning": 0}, "cost": 0.0}

    def make_pending_permission(self, session_id: str, action: str,
                                resources: list[str] | None = None) -> str:
        """测试注入：让会话挂起一条 pending permission（v2 形态 {action,resources}）。"""
        perm_id = f"per_{uuid.uuid4().hex[:12]}"
        sess = self.sessions[session_id]
        sess.pending_permission = {
            "id": perm_id, "sessionID": session_id, "action": action,
            "resources": resources or ["*"], "metadata": {},
        }
        self.emit("permission.asked", {
            "sessionID": session_id, "requestID": perm_id,
            "action": action, "resources": resources or ["*"],
        })
        return perm_id
