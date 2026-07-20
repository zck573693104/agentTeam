"""GET/POST /api/library/agents 端点：专家 Agent 库管理。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agentteam.domain.agent import Agent
from agentteam.domain.library import AgentLibrary
from agentteam.storage.admin_audit import AdminAuditRepo


class AgentDict(BaseModel):
    """POST/PUT /api/library/agents 的请求体。

    完整字段契约(BUG-11 + Arch #3):除基本字段外,必须支持 children/ref/
    mcp_servers,否则通过 API 创建的 library agent 无法携带这些字段
    (Pydantic 会静默丢弃未声明字段)。
    """
    name: str
    role: str
    system_prompt: str = ""
    tools: list[str] = []
    max_iterations: int = 10
    model: dict | None = None
    approval_policy: dict | None = None
    # BUG-11:此前缺失,POST 时被 Pydantic 静默丢弃。
    children: list[dict] = []
    ref: str | None = None
    mcp_servers: list[dict] = []
    # SP7:skills + version 字段
    skills: list[str] = []
    version: int = 1


def _build_agent_from_dict(agent: AgentDict) -> Agent:
    """从 AgentDict 构造 Agent(POST/PUT 共用)。

    委托给 domain.serializer._agent_from_dict,统一字段解析逻辑,
    完整支持 children/ref/mcp_servers(BUG-11)。serializer 中的
    _agent_from_dict 已正确处理 ModelRef/ApprovalPolicy/MCPServer/TeamRef
    的递归解析,无需在此重复实现。
    """
    from agentteam.domain.serializer import _agent_from_dict
    return _agent_from_dict(agent.model_dump())


def library_router(library: AgentLibrary, admin_audit: AdminAuditRepo | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/library", tags=["library"])

    def _audit(event_type: str, agent_name: str, payload: dict | None = None) -> None:
        if admin_audit is not None:
            admin_audit.add_event(event_type, "library_agent", agent_name, payload=payload)

    @router.get("/agents")
    def list_agents():
        # Arch #3:返回完整字段(_agent_to_dict 含 children/ref/mcp_servers/
        # model/approval_policy),客户端能看到 agent 全貌。此前只返回精简字段。
        from agentteam.domain.serializer import _agent_to_dict
        return [_agent_to_dict(a) for a in library.agents.values()]

    @router.post("/agents")
    def register_agent(agent: AgentDict):
        a = _build_agent_from_dict(agent)
        # BUG-02:用原子 register_if_absent 替代 get-then-register,避免并发
        # POST 同名 agent 时 register 内部抛 ValueError 未捕获 → 500。
        if not library.register_if_absent(a):
            raise HTTPException(
                status_code=400,
                detail=f"Agent already exists: {agent.name}",
            )
        _audit("library_agent_created", a.name, {"role": a.role})
        return {"name": a.name}

    @router.put("/agents/{name}")
    def update_agent(name: str, agent: AgentDict):
        if library.get(name) is None:
            raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
        a = _build_agent_from_dict(agent)
        if a.name != name:
            raise HTTPException(
                status_code=400,
                detail=f"Name in body ({a.name}) must match URL ({name})",
            )
        library.update(a)
        _audit("library_agent_updated", a.name, {"role": a.role})
        return {"name": a.name}

    @router.delete("/agents/{name}")
    def delete_agent(name: str):
        if not library.delete(name):
            raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
        _audit("library_agent_deleted", name)
        return {"ok": True}

    return router
