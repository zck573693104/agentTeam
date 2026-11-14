"""Worker 定义(扁平 Worker Pool 的成员,dsh: 每 Ticket 一个 Session + 独立目录)。

与 v1 Agent 树不同,v2 压平为 Router + Worker Pool:
- Router 不做技术决策、无执行工具,批注 ≤30 词
- Worker 是唯一执行者,禁止对人说话(WORKER_DISCIPLINE 兜底)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkerDef:
    """工人池成员定义。model 为 ModelRef dict(None = 用部署默认模型)。"""

    name: str
    system_prompt: str = ""
    model: dict[str, Any] | None = None
    tools: list[str] = field(default_factory=list)   # 工具名(read_file 等文件工具按 workspace 注入,不必声明)
    skills: list[str] = field(default_factory=list)
    # MCP 挂载(MCPServer dict 同构;session 启动时注册到 ToolRegistry,tool 名 mcp:{name}:*)
    mcp_servers: list[dict] = field(default_factory=list)
    # P1 审批门:需要人工批准的工具名;["*"] = 全部工具需批准;空 = 免审批
    approval_targets: list[str] = field(default_factory=list)
    max_iterations: int = 10
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "system_prompt": self.system_prompt,
            "model": self.model,
            "tools": list(self.tools),
            "skills": list(self.skills),
            "mcp_servers": list(self.mcp_servers),
            "approval_targets": list(self.approval_targets),
            "max_iterations": self.max_iterations,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WorkerDef":
        return cls(
            name=d["name"],
            system_prompt=d.get("system_prompt", ""),
            model=d.get("model"),
            tools=list(d.get("tools", [])),
            skills=list(d.get("skills", [])),
            mcp_servers=list(d.get("mcp_servers", [])),
            approval_targets=list(d.get("approval_targets", [])),
            max_iterations=int(d.get("max_iterations", 10)),
            enabled=bool(d.get("enabled", True)),
        )


def worker_from_row(row) -> WorkerDef:
    """SQLite Row → WorkerDef(workers 表列 → dataclass)。"""
    import json

    return WorkerDef(
        name=row["name"],
        system_prompt=row["system_prompt"] or "",
        model=json.loads(row["model_json"]) if row["model_json"] else None,
        tools=json.loads(row["tools_json"] or "[]"),
        skills=json.loads(row["skills_json"] or "[]"),
        mcp_servers=json.loads(row["mcp_json"] or "[]"),
        approval_targets=json.loads(row["approval_json"] or "[]"),
        max_iterations=int(row["max_iterations"]),
        enabled=bool(row["enabled"]),
    )
