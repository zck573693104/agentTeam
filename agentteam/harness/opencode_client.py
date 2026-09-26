"""opencode server v2 (/api/*) REST + SSE 客户端。

契约来源：opencode v1.18.32 本机实测（.tmp_oc_smoke/probe_v2*.py，2026-09-25），
详见 docs/opencode-harness-design.md §2/§8。关键差异（相对 v1 /session 面）：

- `POST /api/session` body {id?, agent?, model:{id,providerID,variant?}, location?}
  —— **不接受 title / permission 规则集**（v2 会话不受 permission 门约束，实测）。
- `POST /api/session/{id}/prompt` body {prompt:{text,files,agents}, delivery, resume}
  —— **无 per-request system / format / tools / model**；`resume:false` 只入队不
  调度回合（实测永久空转，见 prompt_async）；完成信号是消息上的
  `finish: "stop"`（`"tool-calls"` 表示仍在循环中）。
- `GET /api/session/{id}/message` → {data:[{id, type:"assistant"|"user", finish,
  content:[{type:"text",...}|{type:"reasoning"}|{type:"tool",name,state:{status,
  input,content,error},provider:{executed}}], tokens:{...逐消息},
  snapshot:{start,end,files}}]}
  —— **按时间倒序返回**（服务端顺序即权威顺序，客户端不再重排）；会话对象
  `GET /api/session/{id}` 的聚合 tokens **恒为 0**，用量必须逐消息累加。
- 自定义 agent 在 v2 侧不可见（`GET /api/agent` 只列内置 7 个），且会话即使带
  `agent` 创建，其配置里的 `prompt` 也不会被应用（实测 canary 无效）——
  所以 system prompt 只能内联进用户文本，见 translator.worker_user_prompt。
- `POST /api/session/{id}/interrupt` 取代 v1 abort；`POST /api/session/{id}/wait`
  在 1.18.32 仍返回 503（不可用）。
- SSE `GET /api/event` 事件形如 {id, type:"session.next.*", durable?, location, data}，
  **载荷在 data 而非 properties**。工具调用分阶段可见：
  tool.input.started(name) → tool.input.ended(完整入参) → tool.called → tool.success。
  durable 事件的 seq 在 `durable.seq`（history 同构）。
- 仅 MCP 注册与 provider 配置补丁没有 v2 等价端点（无 v2.mcp.* / v2.config.*），
  故 `add_mcp` / `update_config` 仍走 v1 路径。

线程模型沿用 v1：REST 串行化加锁，SSE 独立守护线程 + 指数退避重连，
订阅者回调异常相互隔离。
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests


class OpenCodeError(RuntimeError):
    """opencode server 返回非 2xx 或连接失败。"""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class OpenCodeConfig:
    """opencode server 连接配置。"""

    base_url: str = "http://127.0.0.1:4096"
    password: str | None = None
    # REST 请求超时（秒）。
    timeout: float = 30.0
    # SSE 断线重连退避上限（秒）
    reconnect_max_backoff: float = 8.0


@dataclass
class PermissionRequest:
    """opencode pending permission。

    v2 形态为 {action, resources}，v1 形态为 {permission, patterns}；
    两者都归一化到 permission/patterns（v2 的会话不会产生 pending —— 实测
    v2 会话绕过 permission 门，此类型仅对 v1 会话/MCP 工具仍有意义）。
    """

    id: str
    session_id: str
    permission: str
    patterns: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "PermissionRequest":
        return cls(
            id=raw["id"],
            session_id=raw["sessionID"],
            permission=raw.get("permission") or raw.get("action") or "",
            patterns=list(raw.get("patterns") or raw.get("resources") or []),
            metadata=raw.get("metadata") or {},
        )


class OpenCodeClient:
    """opencode v2 HTTP API 客户端（REST + SSE 订阅）。"""

    def __init__(self, config: OpenCodeConfig | None = None) -> None:
        self._config = config or OpenCodeConfig()
        self._session = requests.Session()
        if self._config.password:
            self._session.auth = ("opencode", self._config.password)
        self._rest_lock = threading.Lock()
        self._subs: list[Callable[[dict], None]] = []
        self._subs_lock = threading.Lock()
        self._stream_thread: threading.Thread | None = None
        self._stream_stop = threading.Event()
        self._stream_started = False
        self._stream_lock = threading.Lock()

    # ---------- REST 基础 ----------

    def _url(self, path: str) -> str:
        return self._config.base_url.rstrip("/") + path

    def _request(self, method: str, path: str, body: dict | None = None,
                 timeout: float | None = None) -> Any:
        try:
            with self._rest_lock:
                resp = self._session.request(
                    method, self._url(path), json=body,
                    timeout=timeout or self._config.timeout,
                )
        except requests.RequestException as e:
            raise OpenCodeError(
                f"opencode server unreachable at {self._config.base_url}: {e}") from e
        if resp.status_code >= 400:
            raise OpenCodeError(
                f"opencode {method} {path} -> {resp.status_code}: {resp.text[:300]}",
                status_code=resp.status_code,
            )
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    @staticmethod
    def _unwrap(data: Any) -> Any:
        """v2 响应普遍包一层 {data: ...}。"""
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return data

    # ---------- 会话 ----------

    def create_session(self, model: dict | None = None,
                       agent: str | None = None) -> dict:
        """POST /api/session。v2 不接受 title / permission，会话级限制无入口。

        model 接受 {providerID, modelID} 或 {providerID, id}，统一成 v2 ModelRef
        {id, providerID}（可选 variant）。
        agent 允许传未注册的名字：server 原样回显且不报错（实测），
        引擎用它携带 worker 名作为会话标签。
        """
        body: dict[str, Any] = {}
        if model:
            body["model"] = {
                "id": model.get("id") or model.get("modelID"),
                "providerID": model["providerID"],
            }
            if model.get("variant"):
                body["model"]["variant"] = model["variant"]
        if agent:
            body["agent"] = agent
        return self._unwrap(self._request("POST", "/api/session", body)) or {}

    def get_session(self, session_id: str) -> dict:
        """GET /api/session/{id}。注意：其 tokens 聚合恒为 0，用量见 messages()。"""
        return self._unwrap(self._request("GET", f"/api/session/{session_id}")) or {}

    def prompt_async(self, session_id: str, text: str, *,
                     delivery: str = "queue", resume: bool = True,
                     files: list[dict] | None = None,
                     agents: list[dict] | None = None) -> Any:
        """POST /api/session/{id}/prompt —— v2 只有异步形态。

        **resume 必须为 True**：spec 原文「schedule agent-loop execution unless
        resume is false」—— 实测 resume=False 时输入只落 durable 队列，回合
        永远不被调度（消息列表恒空、/api/session/active 恒空，无任何报错）。
        完成与否由调用方判断（messages() 里本回合 assistant 的 finish=="stop"），
        /wait 端点在 1.18.32 返回 503 不可用。
        """
        prompt: dict[str, Any] = {"text": text}
        if files:
            prompt["files"] = files
        if agents:
            prompt["agents"] = agents
        return self._request("POST", f"/api/session/{session_id}/prompt",
                             {"prompt": prompt, "delivery": delivery, "resume": resume})

    def messages(self, session_id: str) -> list[dict]:
        """GET /api/session/{id}/message —— **实测按时间倒序返回**（最新在前）。"""
        data = self._unwrap(self._request("GET", f"/api/session/{session_id}/message"))
        return list(data or [])

    @staticmethod
    def turn_message_id(resp: Any) -> str | None:
        """prompt 响应里的用户消息 id —— 后续回合完成度的锚点。

        v2 的完成判定不能只看「最后一条 assistant 是否 finish=stop」：补发
        下一轮 prompt 后，上一轮的 stop 消息仍是最新的 assistant，会被误读成
        「已完成」。故一律以本轮用户消息为锚，只看它之后的 assistant。
        """
        data = OpenCodeClient._unwrap(resp if isinstance(resp, dict) else {}) or {}
        return data.get("id") if isinstance(data, dict) else None

    def _after_turn(self, session_id: str,
                    after_message_id: str | None) -> list[dict]:
        """取「指定用户消息之后」的消息（保持服务端 新→旧 顺序）。

        after_message_id 尚未落库时返回空列表 —— 回合还没开始，自然未完成。
        """
        msgs = self.messages(session_id)
        if after_message_id is None:
            return msgs
        for i, m in enumerate(msgs):
            if m.get("id") == after_message_id:
                return msgs[:i]
        return []

    def assistant_done(self, session_id: str,
                       after_message_id: str | None = None) -> dict | None:
        """本回合是否结束：锚点之后最新的 assistant 且 finish=="stop" 时返回该消息。

        finish=="tool-calls"（或 None）表示 agent loop 还在继续，不算完成。
        finish=="error" 是终止但没产出，用 turn_error() 取原因。
        """
        for m in self._after_turn(session_id, after_message_id):
            if m.get("type") == "assistant":
                return m if (m.get("finish") or "") == "stop" else None
        return None

    def turn_error(self, session_id: str,
                   after_message_id: str | None = None) -> str | None:
        """本回合是否已终止于错误：assistant finish=="error" 时返回错误文本。

        实测（1.18.32 + 免费模型限流）失败回合的形状是 assistant
        `finish:"error"` + `error.message`（HTTP 429 FreeUsageLimitError），
        配套事件是 `session.next.step.failed` —— **不是** `session.error`。
        只等 finish=="stop" 会一直轮询到 prompt 超时（默认 600s），所以这里
        给引擎一条 REST 判定路径（不依赖 SSE）。
        """
        for m in self._after_turn(session_id, after_message_id):
            if m.get("type") != "assistant":
                continue
            if (m.get("finish") or "") != "error":
                return None
            err = m.get("error") or {}
            msg = err.get("message") if isinstance(err, dict) else str(err)
            return str(msg or err or "opencode turn failed")
        return None

    def final_text(self, session_id: str,
                   after_message_id: str | None = None) -> str:
        """本回合最终 assistant 消息的全部 text content（v2 content[].type=="text"）。"""
        for m in self._after_turn(session_id, after_message_id):
            if m.get("type") != "assistant":
                continue
            return "\n".join(
                c.get("text", "") for c in (m.get("content") or [])
                if c.get("type") == "text" and c.get("text", "").strip()
            )
        return ""

    def message_tokens(self, session_id: str) -> int:
        """逐消息 tokens 求和（v2 会话对象聚合恒 0，用量只能这样取）。"""
        total = 0
        for m in self.messages(session_id):
            t = m.get("tokens") or {}
            total += int(t.get("input", 0) or 0) + int(t.get("output", 0) or 0) \
                + int(t.get("reasoning", 0) or 0)
        return total

    def interrupt_session(self, session_id: str) -> Any:
        """POST /api/session/{id}/interrupt —— 中断在飞回合（v1 abort 的 v2 等价物）。"""
        return self._request("POST", f"/api/session/{session_id}/interrupt")

    def history(self, session_id: str, limit: int = 100) -> list[dict]:
        """GET /api/session/{id}/history —— durable 事件（带 seq，可重放/补审计）。

        limit 上限 100（实测 200 → 400 InvalidRequestError）。
        """
        data = self._unwrap(
            self._request("GET", f"/api/session/{session_id}/history?limit={limit}"))
        return list(data or [])

    # ---------- 权限（v1 语义保留；v2 会话实测不产生 pending）----------

    def pending_permissions(self) -> list[PermissionRequest]:
        """GET /api/permission/request —— v2 pending 视图（看不见 v1 会话的请求）。"""
        data = self._unwrap(self._request("GET", "/api/permission/request"))
        return [PermissionRequest.from_api(r) for r in (data or [])]

    def respond_permission(self, session_id: str, request_id: str,
                           response: str) -> bool:
        """POST /api/session/{id}/permission/{rid}/reply，reply ∈ once|always|reject。"""
        return bool(self._request(
            "POST", f"/api/session/{session_id}/permission/{request_id}/reply",
            {"reply": response},
        ))

    # ---------- 无 v2 等价端点：沿用 v1 路径 ----------

    def get_config(self) -> dict:
        return self._unwrap(self._request("GET", "/config")) or {}

    def update_config(self, patch: dict) -> dict:
        """PATCH /config —— provider 补丁可用；agent 注入实测不生效（§7）。"""
        return self._request("PATCH", "/config", patch)

    def list_providers(self) -> list[dict]:
        data = self._unwrap(self._request("GET", "/provider")) or {}
        if isinstance(data, dict):
            return list(data.get("all", []) or data.get("connected", []) or [])
        return list(data or [])

    def add_mcp(self, name: str, config: dict) -> Any:
        """POST /mcp —— v2 无对应端点，MCP 动态挂载仍走 v1。"""
        return self._request("POST", "/mcp", {"name": name, "config": config})

    def health(self) -> Any:
        return self._request("GET", "/api/health")

    def server_version(self) -> str | None:
        """GET /global/health —— server 版本号（v1 端点，v2 面也有响应）。

        用于兼容门（runner.check_backend_compatibility）：opencode 的
        HTTP 契约按版本漂移（v2 线独立渠道、无兼容承诺），引擎只对
        已验证的 1.18.x 线做功能保证。
        """
        data = self._request("GET", "/global/health")
        if isinstance(data, dict):
            data = data.get("data", data)
        if isinstance(data, dict):
            return data.get("version")
        return None

    # ---------- SSE ----------

    def subscribe(self, callback: Callable[[dict], None]) -> None:
        """注册 SSE 事件回调，并确保事件流线程已启动。"""
        with self._subs_lock:
            self._subs.append(callback)
        self._ensure_stream()

    def unsubscribe(self, callback: Callable[[dict], None]) -> None:
        with self._subs_lock:
            try:
                self._subs.remove(callback)
            except ValueError:
                pass

    def _ensure_stream(self) -> None:
        with self._stream_lock:
            if self._stream_started and self._stream_thread and self._stream_thread.is_alive():
                return
            self._stream_stop.clear()
            self._stream_thread = threading.Thread(
                target=self._stream_loop, name="opencode-sse", daemon=True
            )
            self._stream_started = True
            self._stream_thread.start()

    def stop_stream(self) -> None:
        self._stream_stop.set()

    def _stream_loop(self) -> None:
        """SSE 长连接循环：解析 `data:` 行 → JSON → 分发订阅者；断线退避重连。

        事件流只在有订阅者时维持连接；订阅者全部退订后线程自然退出。
        """
        backoff = 0.5
        while not self._stream_stop.is_set():
            with self._subs_lock:
                has_subs = bool(self._subs)
            if not has_subs:
                time.sleep(0.2)
                continue
            try:
                resp = self._session.get(
                    self._url("/api/event"), stream=True, timeout=(5, None),
                    headers={"Accept": "text/event-stream"},
                )
                if resp.status_code >= 400:
                    raise OpenCodeError(
                        f"SSE /api/event -> {resp.status_code}", resp.status_code)
                backoff = 0.5
                for payload in self._iter_sse(resp):
                    if self._stream_stop.is_set():
                        break
                    self._dispatch(payload)
                resp.close()
            except Exception:
                # 连接失败/读取中断：退避后重连。server 未起时静默轮询。
                pass
            if self._stream_stop.is_set():
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, self._config.reconnect_max_backoff)

    @staticmethod
    def _iter_sse(resp):
        """SSE 帧解析：只关心 data: 行，空行分帧。

        用 raw.read1() 而非 iter_lines()：后者在无 Content-Length 的流上会攒满
        chunk 才返回，SSE 长连接下事件被无限期缓冲。
        """
        data_lines: list[bytes] = []
        buf = b""
        raw = resp.raw
        while True:
            try:
                chunk = raw.read1(65536)
            except Exception:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.rstrip(b"\r").strip()
                if not line:
                    if data_lines:
                        yield b"".join(data_lines)
                        data_lines = []
                    continue
                if line.startswith(b"data:"):
                    data_lines.append(line[5:].lstrip())
        if data_lines:
            yield b"".join(data_lines)

    def _dispatch(self, payload: bytes) -> None:
        try:
            event = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(event, dict):
            return
        with self._subs_lock:
            subs = list(self._subs)
        for cb in subs:
            try:
                cb(event)
            except Exception:
                # 单个订阅者异常不拖垮事件流
                pass

    def close(self) -> None:
        self.stop_stream()
        with self._rest_lock:
            self._session.close()
