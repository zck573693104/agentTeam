"""Team dataclass 与 JSON dict 之间的双向转换。

支持新旧 schema：
- 新 schema：dict 含 root（Agent 树）
- 旧 schema：dict 含 leader + workers（自动转 root）
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from agentteam.domain.agent import Agent, TeamRef
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


def _agent_from_dict(d: dict) -> Agent:
    children: list[Agent | TeamRef] = []
    for c in d.get("children", []):
        if c.get("_type") == "TeamRef":
            children.append(TeamRef(
                name=c["name"],
                alias=c.get("alias"),
                mcp_overrides=[_mcp_server_from_dict(s) for s in c.get("mcp_overrides", [])],
            ))
        else:
            children.append(_agent_from_dict(c))
    return Agent(
        name=d["name"],
        role=d["role"],
        system_prompt=d.get("system_prompt", ""),
        model=_model_ref_from_dict(d.get("model")),
        children=children,
        approval_policy=_approval_policy_from_dict(d.get("approval_policy")),
        tools=d.get("tools", []),
        max_iterations=d.get("max_iterations", 10),
        ref=d.get("ref"),
        mcp_servers=[_mcp_server_from_dict(s) for s in d.get("mcp_servers", [])],
    )


def _agent_to_dict(agent: Agent) -> dict:
    children = []
    for c in agent.children:
        if isinstance(c, TeamRef):
            children.append({
                "_type": "TeamRef",
                "name": c.name,
                "alias": c.alias,
                "mcp_overrides": [asdict(s) for s in c.mcp_overrides],
            })
        else:
            children.append(_agent_to_dict(c))
    return {
        "name": agent.name,
        "role": agent.role,
        "system_prompt": agent.system_prompt,
        "model": asdict(agent.model) if agent.model else None,
        "children": children,
        "approval_policy": asdict(agent.approval_policy) if agent.approval_policy else None,
        "tools": list(agent.tools),
        "max_iterations": agent.max_iterations,
        "ref": agent.ref,
        "mcp_servers": [asdict(s) for s in agent.mcp_servers],
    }


def team_to_dict(team: Team) -> dict[str, Any]:
    """Team → JSON-serializable dict（新 schema：含 root 字段）。"""
    return {
        "name": team.name,
        "description": team.description,
        "root": _agent_to_dict(team.root),
        "default_model": asdict(team.default_model),
        "skills": list(team.skills),
        "mcp_servers": [asdict(s) for s in team.mcp_servers],
    }


def team_from_dict(data: dict[str, Any]) -> Team:
    """dict → Team，支持新旧 schema。

    - 新 schema：data 含 'root' 字段
    - 旧 schema：data 含 'leader' + 'workers' 字段，自动转 root
    """
    if "root" in data:
        return Team(
            name=data["name"],
            description=data["description"],
            root=_agent_from_dict(data["root"]),
            default_model=_model_ref_from_dict(data["default_model"]),  # type: ignore[arg-type]
            skills=data.get("skills", []),
            mcp_servers=[_mcp_server_from_dict(s) for s in data.get("mcp_servers", [])],
        )
    # 旧 schema
    leader = _leader_from_dict(data["leader"])
    workers = [_worker_from_dict(w) for w in data["workers"]]
    return Team.from_legacy(
        name=data["name"],
        description=data["description"],
        leader=leader,
        workers=workers,
        default_model=_model_ref_from_dict(data["default_model"]),  # type: ignore[arg-type]
        skills=data.get("skills", []),
        mcp_servers=[_mcp_server_from_dict(s) for s in data.get("mcp_servers", [])],
    )
