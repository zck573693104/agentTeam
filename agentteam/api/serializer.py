"""Team dataclass 与 JSON dict 之间的双向转换。"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.mcp_server import MCPServer
from agentteam.domain.team import Leader, Team
from agentteam.domain.worker import Worker
from agentteam.models.provider import ModelRef


def _model_ref_from_dict(d: dict | None) -> ModelRef | None:
    if d is None:
        return None
    return ModelRef(
        provider=d["provider"],
        name=d["name"],
        temperature=d.get("temperature", 0.7),
        streaming=d.get("streaming", True),
    )


def _approval_policy_from_dict(d: dict | None) -> ApprovalPolicy | None:
    if d is None:
        return None
    return ApprovalPolicy(
        level=d["level"],
        targets=d.get("targets"),
        timeout_seconds=d.get("timeout_seconds"),
    )


def _leader_from_dict(d: dict) -> Leader:
    return Leader(
        name=d.get("name", "leader"),
        role=d.get("role", "主管"),
        system_prompt=d.get("system_prompt", ""),
        model=_model_ref_from_dict(d.get("model")),
        approval_policy=_approval_policy_from_dict(d.get("approval_policy")),
    )


def _worker_from_dict(d: dict) -> Worker:
    return Worker(
        name=d["name"],
        role=d["role"],
        description=d.get("description", ""),
        system_prompt=d.get("system_prompt", ""),
        model=_model_ref_from_dict(d.get("model")),
        tools=d.get("tools", []),
        approval_policy=_approval_policy_from_dict(d.get("approval_policy")),
        max_iterations=d.get("max_iterations", 10),
    )


def _mcp_server_from_dict(d: dict) -> MCPServer:
    return MCPServer(
        name=d["name"],
        command=d["command"],
        args=d.get("args", []),
        env=d.get("env", {}),
        transport=d.get("transport", "stdio"),
        url=d.get("url"),
    )


def team_to_dict(team: Team) -> dict[str, Any]:
    """Team dataclass → JSON-serializable dict。"""
    return asdict(team)


def team_from_dict(data: dict[str, Any]) -> Team:
    """dict → Team，手动重建嵌套对象。"""
    return Team(
        name=data["name"],
        description=data["description"],
        leader=_leader_from_dict(data["leader"]),
        workers=[_worker_from_dict(w) for w in data["workers"]],
        default_model=_model_ref_from_dict(data["default_model"]),  # type: ignore[arg-type]
        skills=data.get("skills", []),
        mcp_servers=[_mcp_server_from_dict(s) for s in data.get("mcp_servers", [])],
    )
