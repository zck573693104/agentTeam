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
from agentteam.harness.opencode_client import OpenCodeClient, OpenCodeConfig


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


def ensure_backend(client: OpenCodeClient, team, default_model: str) -> None:
    """opencode 引导：provider 配置补丁 + MCP 注册（幂等，容忍 server 缺失）。

    失败不抛异常由调用方决定？——否：连接失败必须在 run 提交时暴露
    （fail-fast），这里只吞「provider 已存在 / MCP 重名」类幂等冲突。
    """
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
