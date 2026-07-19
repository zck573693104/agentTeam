"""SP7b Evolution API:history 查询 + rollback。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from agentteam.domain.library import AgentLibrary
from agentteam.storage.evolution import EvolutionRepo


def evolution_router(evolution_repo: EvolutionRepo, agent_library: AgentLibrary) -> APIRouter:
    """构造 /api/agents evolution 相关路由。

    endpoints:
    - GET  /api/agents/{name}/history          — 查询 agent 进化历史
    - GET  /api/agents/{name}/versions/{v}     — 取指定 version 快照
    - POST /api/agents/{name}/rollback?v=N     — 回滚到 version N
    """
    router = APIRouter(prefix="/api/agents", tags=["evolution"])

    @router.get("/{agent_name}/history")
    def list_history(agent_name: str, limit: int = 20):
        return {"history": evolution_repo.list_history(agent_name, limit)}

    @router.get("/{agent_name}/versions/{version}")
    def get_version(agent_name: str, version: int):
        records = evolution_repo.get_version_snapshot(agent_name, version)
        if not records:
            raise HTTPException(status_code=404, detail=f"Version {version} not found")
        return {"version": version, "records": records}

    @router.post("/{agent_name}/rollback")
    def rollback_agent(agent_name: str, version: int):
        # 1. 取目标 version 的所有 history 记录
        records = evolution_repo.get_version_snapshot(agent_name, version)
        if not records:
            raise HTTPException(status_code=404, detail=f"Version {version} not found")

        # 2. 把 before_value 应用回 Agent
        agent = agent_library.get(agent_name)
        if agent is None:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

        for record in records:
            dimension = record.get("dimension")
            before_value = record.get("before_value", "")
            if dimension == "prompt":
                agent.system_prompt = before_value
            elif dimension == "params":
                try:
                    params = json.loads(before_value)
                    if "max_iterations" in params:
                        agent.max_iterations = params["max_iterations"]
                    if "approval_policy" in params:
                        # approval_policy 已是 dict,转回 ApprovalPolicy 对象
                        from agentteam.domain.approval import ApprovalPolicy
                        ap = params["approval_policy"]
                        if isinstance(ap, dict):
                            agent.approval_policy = ApprovalPolicy(**ap)
                        else:
                            agent.approval_policy = ap
                except (json.JSONDecodeError, TypeError):
                    pass  # 跳过损坏的 params 记录
            # skill_gen / skill_select 不回滚(已写入文件的 skill 保留)

        # 3. 写新 history 记录(类型: rollback)
        new_version = agent.version + 1
        evolution_repo.add_record(
            agent_name=agent_name, version=new_version, dimension="rollback",
            before_value=f"v{agent.version}", after_value=f"v{version}",
            diff="", reason=f"User rolled back to v{version}",
            run_id=None, success=True,
        )
        agent.version = new_version
        agent_library.update_version(agent_name, new_version)
        return {"ok": True, "new_version": new_version}

    return router
