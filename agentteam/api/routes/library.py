"""GET/POST /api/library/agents 端点：专家 Agent 库管理。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agentteam.domain.agent import Agent
from agentteam.domain.library import AgentLibrary


class AgentDict(BaseModel):
    name: str
    role: str
    system_prompt: str = ""
    tools: list[str] = []
    max_iterations: int = 10
    model: dict | None = None
    approval_policy: dict | None = None


def library_router(library: AgentLibrary) -> APIRouter:
    router = APIRouter(prefix="/api/library", tags=["library"])

    @router.get("/agents")
    def list_agents():
        return [
            {"name": a.name, "role": a.role, "system_prompt": a.system_prompt,
             "tools": list(a.tools), "max_iterations": a.max_iterations}
            for a in library.agents.values()
        ]

    @router.post("/agents")
    def register_agent(agent: AgentDict):
        from agentteam.domain.approval import ApprovalPolicy
        from agentteam.models.provider import ModelRef
        existing = library.get(agent.name)
        if existing is not None:
            raise HTTPException(status_code=400, detail=f"Agent already exists: {agent.name}")
        model = None
        if agent.model:
            model = ModelRef(
                provider=agent.model["provider"],
                name=agent.model["name"],
                temperature=agent.model.get("temperature", 0.7),
                streaming=agent.model.get("streaming", True),
            )
        ap = None
        if agent.approval_policy:
            ap = ApprovalPolicy(
                level=agent.approval_policy["level"],
                targets=agent.approval_policy.get("targets"),
                timeout_seconds=agent.approval_policy.get("timeout_seconds"),
            )
        a = Agent(
            name=agent.name, role=agent.role,
            system_prompt=agent.system_prompt,
            tools=list(agent.tools), max_iterations=agent.max_iterations,
            model=model, approval_policy=ap,
        )
        library.register(a)
        return {"name": a.name}

    return router
