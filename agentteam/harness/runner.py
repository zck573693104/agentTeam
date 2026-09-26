"""Harness 引擎的组装层：状态持久化 + runner 工厂 + opencode 引导。

- HarnessStateStore：编排快照落 SQLite（run_engine_state 表），与 LangGraph
  的 SqliteSaver checkpoint 对等 —— 服务重启后 approve 仍可续跑。
- HarnessEngineFactory：按 Team 构造 HarnessRunner（共享一条 opencode client、
  注册表、团队注册），供 routes/runs.py 的 create_run 与 approve 的
  rehydrate 路径使用。
- ensure_backend：provider 配置补丁（PATCH /config）+ 团队级/agent 级 MCP
  注册（POST /mcp），幂等。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

from agentteam.harness.engine import HarnessRunner
from agentteam.harness.opencode_client import (
    OpenCodeClient,
    OpenCodeConfig,
    OpenCodeError,
)


class HarnessStateStore:
    """run_engine_state 表读写：run_id → 编排快照 JSON。"""

    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock | None = None) -> None:
        self._conn = conn
        self._lock = lock or threading.Lock()

    def save(self, run_id: str, state: dict) -> None:
        payload = json.dumps(state, ensure_ascii=False, default=str)
        with self._lock:
            self._conn.execute(
                "INSERT INTO run_engine_state (run_id, state) VALUES (?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET state = excluded.state",
                (run_id, payload),
            )
            self._conn.commit()

    def load(self, run_id: str) -> dict | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT state FROM run_engine_state WHERE run_id = ?", (run_id,)
            )
            row = cur.fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["state"])
        except (ValueError, TypeError):
            return None

    def clear(self, run_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM run_engine_state WHERE run_id = ?", (run_id,)
            )
            self._conn.commit()


class HarnessEngineFactory:
    """构造 HarnessRunner（依赖注入点，routes/runs.py 与 rehydrate 共用）。"""

    def __init__(
        self,
        client: OpenCodeClient,
        default_model: str,
        skill_loader=None,
        library=None,
        state_store: HarnessStateStore | None = None,
        prompt_timeout: float = 600.0,
        poll_interval: float = 0.25,
    ) -> None:
        self._client = client
        self._default_model = default_model
        self._skill_loader = skill_loader
        self._library = library
        self._state_store = state_store
        self._prompt_timeout = prompt_timeout
        self._poll_interval = poll_interval
        self._team_registry: dict[str, Any] = {}
        self._registry_lock = threading.Lock()

    @property
    def client(self) -> OpenCodeClient:
        return self._client

    def register_team(self, team) -> None:
        """注册 Team（供 TeamRef 解析，与 TeamCompiler.register_team 对等）。"""
        with self._registry_lock:
            self._team_registry[team.name] = team

    def set_teams(self, teams: list) -> None:
        with self._registry_lock:
            self._team_registry = {t.name: t for t in teams}

    def create(
        self,
        run_id: str,
        team,
        task: str,
        trace_writer,
        audit_repo,
        run_manager=None,
    ) -> HarnessRunner:
        return HarnessRunner(
            client=self._client,
            run_id=run_id,
            team=team,
            task=task,
            trace_writer=trace_writer,
            audit_repo=audit_repo,
            skill_loader=self._skill_loader,
            library=self._library,
            team_registry=dict(self._team_registry),
            run_manager=run_manager,
            state_store=self._state_store,
            default_model=self._default_model,
            prompt_timeout=self._prompt_timeout,
            poll_interval=self._poll_interval,
        )

    def rehydrate(
        self,
        run_id: str,
        team,
        task: str,
        trace_writer,
        audit_repo,
        run_manager=None,
    ) -> HarnessRunner:
        """服务重启后重建 runner（invoke 时从 state_store 恢复快照续跑）。"""
        return self.create(run_id, team, task, trace_writer, audit_repo, run_manager)


# 已验证的 opencode 版本线。HTTP 契约按版本漂移（v2 /api/* 是 experimental
# 独立渠道、无兼容承诺；v1 的 tools/deny/wait 缺陷在 1.18.32 均无修复），
# 引擎的功能保证只覆盖 KNOWN_GOOD 所在线；偏离时按级别告警/拒绝。
KNOWN_GOOD_VERSION = "1.18.32"
_KNOWN_MAJOR = 1
_KNOWN_MINOR = 18


def check_backend_compatibility(client: OpenCodeClient) -> dict[str, Any]:
    """探测 server 版本并给出兼容性判定。

    返回 {"version", "level", "message"}；level ∈ ok / warn / error：
    - error：主版本偏离（如 v2 独立渠道 / 未来 2.x）——契约不兼容，fail-fast
    - warn ：小版本偏离（更旧=未经测试；更新=契约漂移风险，tools/deny/
             structured-output 的行为可能在后续版本变化）——可继续，调用方
             应把 warning 记入 run 审计
    - ok   ：同 1.18.x 线
    版本号缺失/不可解析按 warn 处理（server 可能是未按契约返回的变体）。
    """
    version = client.server_version()
    info: dict[str, Any] = {"version": version, "level": "ok", "message": ""}
    if not version:
        info["level"] = "warn"
        info["message"] = (
            "opencode server 未报告版本号（/global/health 无 version 字段），"
            f"契约兼容性未知；已验证版本线为 {KNOWN_GOOD_VERSION}"
        )
        return info
    try:
        parts = [int(p) for p in version.split(".")[:3]]
        major, minor = parts[0], parts[1]
    except (ValueError, IndexError):
        info["level"] = "warn"
        info["message"] = (
            f"opencode server 版本号不可解析: {version!r}；"
            f"已验证版本线为 {KNOWN_GOOD_VERSION}"
        )
        return info
    if major != _KNOWN_MAJOR:
        info["level"] = "error"
        info["message"] = (
            f"opencode server 主版本 {major}.x 不受支持（引擎契约基于 "
            f"{KNOWN_GOOD_VERSION}；v2/2.x 渠道为 experimental 且无兼容承诺）。"
            f"请安装 1.18.x：npm i -g opencode-ai@1.18.32"
        )
        return info
    if minor != _KNOWN_MINOR:
        info["level"] = "warn"
        if minor < _KNOWN_MINOR:
            info["message"] = (
                f"opencode server {version} 旧于已验证版本线 "
                f"{KNOWN_GOOD_VERSION}，未经测试，建议升级"
            )
        else:
            info["message"] = (
                f"opencode server {version} 新于已验证版本线 "
                f"{KNOWN_GOOD_VERSION}，HTTP 契约可能漂移"
                "（tools/deny/structured-output 行为已知随版本变化），"
                "建议钉住 1.18.x 或重跑 tests/harness/test_real_opencode.py"
            )
    return info


def ensure_backend(
    client: OpenCodeClient, team, default_model: str
) -> dict[str, Any]:
    """opencode 引导：版本兼容门 + provider 配置补丁 + MCP 注册（幂等）。

    返回兼容性判定 dict（level/message），调用方决定是否记入 run 审计。
    这两项配置**只能走 v1 面**：v2 `/api/*` 没有 config/mcp 写入端点（实测）。
    连接失败/主版本不兼容在 run 提交时 fail-fast；只吞
    「provider 已存在 / MCP 重名」类幂等冲突。
    """
    compat = check_backend_compatibility(client)
    if compat["level"] == "error":
        raise OpenCodeError(f"opencode 版本不兼容: {compat['message']}")
    if compat["level"] == "warn":
        import warnings as _w
        _w.warn(f"opencode backend: {compat['message']}", stacklevel=2)

    from agentteam.harness import translator

    patch = translator.provider_patch_for(
        team.default_model, default_model
    )
    if patch:
        try:
            client.update_config({"provider": patch})
        except OpenCodeError as e:
            # 配置可能已被其他 run 注入过；409/400 视为已存在
            if e.status_code not in (400, 409):
                raise

    existing: dict[str, Any] = {}
    try:
        status = client._request("GET", "/mcp")
        if isinstance(status, dict):
            existing = status.get("data", status)
    except OpenCodeError:
        existing = {}

    servers = list(team.mcp_servers or [])
    for child in _walk_agents(team.root):
        servers.extend(child.mcp_servers or [])
    for server in servers:
        if server.name in existing:
            continue
        client.add_mcp(server.name, translator.mcp_to_opencode(server))
    return compat


def _walk_agents(root):
    """遍历 Agent 树（含 TeamRef 展开前的直接引用，MCP 收集用）。"""
    stack = [root]
    seen: set[int] = set()
    while stack:
        agent = stack.pop()
        if id(agent) in seen:
            continue
        seen.add(id(agent))
        yield agent
        for child in agent.children:
            if hasattr(child, "children"):
                stack.append(child)


def default_oc_config_from_env() -> tuple[OpenCodeConfig, str]:
    """从环境变量构造连接配置与默认模型（见设计文档 §5）。"""
    import os

    cfg = OpenCodeConfig(
        base_url=os.environ.get("AGENTTEAM_OPENCODE_URL", "http://127.0.0.1:4096"),
        password=os.environ.get("OPENCODE_SERVER_PASSWORD") or None,
        timeout=float(os.environ.get("AGENTTEAM_OC_REST_TIMEOUT", "30")),
    )
    default_model = os.environ.get(
        "AGENTTEAM_OC_MODEL", "opencode/ling-3.0-flash-fin-free"
    )
    return cfg, default_model
