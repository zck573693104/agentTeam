"""SP7b Evolution API:history 查询 + rollback。"""
from __future__ import annotations

import json
from dataclasses import replace

from fastapi import APIRouter, HTTPException

from agentteam.domain.agent import Agent
from agentteam.domain.approval import ApprovalPolicy
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

        # 2. 取当前 agent(None → 404)
        agent = agent_library.get(agent_name)
        if agent is None:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

        # 3. 计算回滚后的字段值(不直接 mutate agent,避免锁外并发风险)
        # 用 replace 创建新 Agent,所有 mutation 收敛到 agent_library.update 的锁内。
        new_version = agent.version + 1
        updates: dict = {"version": new_version}
        for record in records:
            dimension = record.get("dimension")
            before_value = record.get("before_value", "")
            if dimension == "prompt":
                updates["system_prompt"] = before_value
            elif dimension == "params":
                try:
                    params = json.loads(before_value)
                    if "max_iterations" in params:
                        # 类型防御:与 ParamTuner 对齐(max(1, min(20, int(float(raw)))))
                        raw = params["max_iterations"]
                        try:
                            updates["max_iterations"] = max(1, min(20, int(float(raw))))
                        except (TypeError, ValueError):
                            pass  # 跳过损坏的 max_iterations
                    if "approval_policy" in params:
                        ap = params["approval_policy"]
                        if isinstance(ap, dict):
                            updates["approval_policy"] = ApprovalPolicy(**ap)
                        else:
                            updates["approval_policy"] = ap
                except (json.JSONDecodeError, TypeError):
                    pass  # 跳过损坏的 params 记录
            # skill_gen / skill_select 不回滚(已写入文件的 skill 保留)

        # 4. 构造新 Agent 并原子替换(锁内,避免并发覆盖 daemon thread 的 mutation)
        new_agent = replace(agent, **updates)
        if not agent_library.update(new_agent):
            # update 返回 False 表示 agent 不存在(已被并发删除)
            raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

        # 5. 写 rollback history 记录(在 agent 更新成功之后)
        # 顺序:先 update agent,再写 history。若 history 写入失败,agent 状态正确,
        # 仅缺少审计记录(可接受);若先写 history 再 update,history 可能误导。
        evolution_repo.add_record(
            agent_name=agent_name, version=new_version, dimension="rollback",
            before_value=f"v{agent.version}", after_value=f"v{version}",
            diff="", reason=f"User rolled back to v{version}",
            run_id=None, success=True,
        )
        return {"ok": True, "new_version": new_version}

    return router
