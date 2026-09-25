"""opencode server REST + SSE 客户端。

契约来源：opencode v1.18.32 `GET /doc`（OpenAPI 3.1）实测，见
docs/opencode-harness-design.md §2。关键点：

- `POST /session` body: {title, agent, model, permission, parentID}
- `POST /session/{id}/message`（同步 prompt，返回完整 assistant 消息）
- `POST /session/{id}/prompt_async`（异步 prompt，完成以事件/轮询为准）
- `POST /session/{id}/permissions/{pid}` body: {response: once|always|reject}
- `GET /permission` 列出全部 pending permission（轮询用）
- `GET /session/status` 忙碌会话 map（轮询用）
- `GET /event` SSE 全局事件流，事件形如 {id, type, properties}
- v2 `POST /api/session/{id}/wait` 在 1.18.32 返回 503（未实现），不可依赖

线程模型：REST 调用线程安全（requests.Session 非线程安全，故每次调用加锁）；
SSE 用独立守护线程，断线指数退避重连，订阅者回调异常相互隔离。
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
    # REST 请求超时（秒）。prompt 同步调用可能长达数分钟，调用方自行放宽。
    timeout: float = 30.0
    # SSE 断线重连退避上限（秒）
    reconnect_max_backoff: float = 8.0


@dataclass
class PermissionRequest:
    """opencode pending permission（GET /permission 条目）。"""

    id: str
    session_id: str
    permission: str  # 权限类别，如 "bash" / "edit" / MCP 工具名
    patterns: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "PermissionRequest":
        return cls(
            id=raw["id"],
            session_id=raw["sessionID"],
            permission=raw.get("permission", ""),
            patterns=list(raw.get("patterns", []) or []),
            metadata=raw.get("metadata") or {},
        )


class OpenCodeClient:
    """opencode HTTP API 客户端（REST + SSE 订阅）。"""

    def __init__(self, config: OpenCodeConfig | None = None) -> None:
        self._config = config or OpenCodeConfig()
        self._session = requests.Session()
        if self._config.password:
            self._session.auth = ("opencode", self._config.password)
        self._rest_lock = threading.Lock()
        # SSE 订阅者注册表：callback(event: dict)。异常逐个隔离。
        self._subs: list[Callable[[dict], None]] = []
        self._subs_lock = threading.Lock()
        self._stream_thread: threading.Thread | None = None
        self._stream_stop = threading.Event()
        self._stream_started = False
        self._stream_lock = threading.Lock()

    # ---------- REST ----------

    def _url(self, path: str) -> str:
        return self._config.base_url.rstrip("/") + path

    def _request(self, method: str, path: str, body: dict | None = None, timeout: float | None = None) -> Any:
        try:
            with self._rest_lock:
                resp = self._session.request(
                    method,
                    self._url(path),
                    json=body,
                    timeout=timeout or self._config.timeout,
                )
        except requests.RequestException as e:
            raise OpenCodeError(f"opencode server unreachable at {self._config.base_url}: {e}") from e
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

    def health(self) -> Any:
        """GET /global/health —— 存活探测。"""
        return self._request("GET", "/global/health")

    def get_config(self) -> dict:
        return self._request("GET", "/config") or {}

    def update_config(self, patch: dict) -> dict:
        """PATCH /config —— 合并语义由 opencode 决定（provider/mcp/agent 增量注入）。"""
        return self._request("PATCH", "/config", patch)

    def list_providers(self) -> list[dict]:
        data = self._request("GET", "/provider") or {}
        return list(data.get("all", []) or [])

    def add_mcp(self, name: str, config: dict) -> Any:
        """POST /mcp —— 动态挂载 MCP server（local: {type,command,environment} / remote: {type,url,headers}）。"""
        return self._request("POST", "/mcp", {"name": name, "config": config})

    def create_session(
        self,
        title: str | None = None,
        agent: str | None = None,
        model: dict | None = None,
        permission: list[dict] | None = None,
        parent_id: str | None = None,
    ) -> dict:
        """POST /session。permission 为 PermissionRule 列表。

        model 统一接受 {providerID, modelID} 并转换为会话级契约
        {providerID, id}（OpenAPI: session.model 要求 id 字段且
        additionalProperties=false，与 prompt 级 {providerID, modelID} 不同）。
        """
        body: dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if agent is not None:
            body["agent"] = agent
        if model is not None:
            body["model"] = {
                "providerID": model["providerID"],
                "id": model.get("id") or model.get("modelID"),
            }
        if permission:
            body["permission"] = permission
        if parent_id is not None:
            body["parentID"] = parent_id
        return self._request("POST", "/session", body)

    def get_session(self, session_id: str) -> dict:
        """GET /session/{id} —— 含 tokens/cost 聚合。"""
        return self._request("GET", f"/session/{session_id}")

    def abort_session(self, session_id: str) -> bool:
        """POST /session/{id}/abort —— 取消在飞 prompt。"""
        return bool(self._request("POST", f"/session/{session_id}/abort", {}))

    def delete_session(self, session_id: str) -> bool:
        return bool(self._request("DELETE", f"/session/{session_id}"))

    def messages(self, session_id: str) -> list[dict]:
        """GET /session/{id}/message —— [{info: Message, parts: [Part]}] 按时间序。"""
        data = self._request("GET", f"/session/{session_id}/message")
        if isinstance(data, dict):
            data = data.get("data", [])
        return list(data or [])

    def prompt(self, session_id: str, text: str, *, system: str | None = None,
               agent: str | None = None, model: dict | None = None,
               tools: dict[str, bool] | None = None, fmt: dict | None = None,
               timeout: float | None = None) -> dict:
        """同步 prompt：POST /session/{id}/message，返回 {"info", "parts"}。

        阻塞到模型回合结束。需要中途响应 permission/取消时改用 prompt_async。
        """
        body: dict[str, Any] = {"parts": [{"type": "text", "text": text}]}
        if system is not None:
            body["system"] = system
        if agent is not None:
            body["agent"] = agent
        if model is not None:
            body["model"] = model
        if tools is not None:
            body["tools"] = tools
        if fmt is not None:
            body["format"] = fmt
        return self._request(
            "POST", f"/session/{session_id}/message", body,
            timeout=timeout or self._config.timeout,
        )

    def prompt_async(self, session_id: str, text: str, *, system: str | None = None,
                     agent: str | None = None, model: dict | None = None,
                     tools: dict[str, bool] | None = None, fmt: dict | None = None) -> Any:
        """异步 prompt：立即返回（SessionInputAdmitted）。完成状态由调用方轮询/SSE 判断。"""
        body: dict[str, Any] = {"parts": [{"type": "text", "text": text}]}
        if system is not None:
            body["system"] = system
        if agent is not None:
            body["agent"] = agent
        if model is not None:
            body["model"] = model
        if tools is not None:
            body["tools"] = tools
        if fmt is not None:
            body["format"] = fmt
        return self._request("POST", f"/session/{session_id}/prompt_async", body)

    def pending_permissions(self) -> list[PermissionRequest]:
        """GET /permission —— 全部 pending permission（跨会话）。"""
        data = self._request("GET", "/permission")
        if isinstance(data, dict):
            data = data.get("data", [])
        return [PermissionRequest.from_api(r) for r in (data or [])]

    def respond_permission(self, session_id: str, request_id: str, response: str) -> bool:
        """POST /session/{id}/permissions/{pid}，response ∈ once|always|reject。"""
        return bool(self._request(
            "POST", f"/session/{session_id}/permissions/{request_id}",
            {"response": response},
        ))

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

        事件流只在有订阅者时维持连接；订阅者全部退订后线程自然退出
        （避免对无 opencode 环境 FaN 无谓连接）。
        """
        backoff = 0.5
        while not self._stream_stop.is_set():
            with self._subs_lock:
                has_subs = bool(self._subs)
            if not has_subs:
                time.sleep(0.2)
                continue
            try:
                with self._rest_lock:
                    resp = self._session.get(
                        self._url("/event"),
                        stream=True,
                        timeout=(5, None),  # 连接 5s，读无限（长连接）
                    )
                if resp.status_code >= 400:
                    raise OpenCodeError(f"SSE /event -> {resp.status_code}", resp.status_code)
                backoff = 0.5
                for payload in self._iter_sse(resp):
                    if self._stream_stop.is_set():
                        break
                    self._dispatch(payload)
            except Exception:
                # 连接失败/读取中断：退避后重连。server 未起时静默轮询。
                pass
            if self._stream_stop.is_set():
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, self._config.reconnect_max_backoff)

    @staticmethod
    def _iter_sse(resp):
        """极简 SSE 解析：只关心 data: 行，空行分帧。跨平台兼容 \\r\\n。

        用 raw.read1() 而非 iter_lines()：后者在无 Content-Length 的流上
        会攒满 chunk 才返回（BufferedReader.read(amt) 语义），SSE 长连接
        下事件被无限期缓冲；read1() 有数据即返回，逐帧实时。
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
