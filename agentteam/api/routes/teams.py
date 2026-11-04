"""GET/POST/DELETE /api/teams 端点。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from agentteam.domain.serializer import team_from_dict, team_to_dict
from agentteam.api.store import TeamStore


def teams_router(
    store: TeamStore, memory_repo=None, run_repo=None, audit_repo=None,
) -> APIRouter:
    router = APIRouter(prefix="/api/teams", tags=["teams"])

    @router.get("")
    def list_teams():
        return [team_to_dict(t) for t in store.list_all()]

    @router.post("")
    def register_team(body: dict):
        try:
            team = team_from_dict(body)
        except (KeyError, TypeError) as e:
            raise HTTPException(status_code=422, detail=f"Invalid team JSON: {e}")
        # BUG-01:用原子 register_if_absent 替代 get-then-register,
        # 避免并发 POST 同名 team 时两者都通过检查、互相覆盖。
        if not store.register_if_absent(team):
            raise HTTPException(
                status_code=400,
                detail=f"Team already exists: {team.name}",
            )
        return {"name": team.name}

    @router.get("/{name}")
    def get_team(name: str):
        team = store.get(name)
        if team is None:
            raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
        return team_to_dict(team)

    @router.delete("/{name}")
    def delete_team(name: str):
        if not store.delete(name):
            raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
        return {"ok": True}

    @router.put("/{name}")
    def update_team(name: str, body: dict):
        if store.get(name) is None:
            raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
        try:
            team = team_from_dict(body)
        except (KeyError, TypeError) as e:
            raise HTTPException(status_code=422, detail=f"Invalid team JSON: {e}")
        if team.name != name:
            raise HTTPException(
                status_code=400,
                detail=f"Name in body ({team.name}) must match URL ({name})",
            )
        store.update(team)
        # SP12 Mandate 策略版本化:团队配置(审批策略/成员模型)热更新 →
        # policy_version 变更 → 进行中 run 记「授权依据已过期」注记。
        # 不中断运行(对齐 dsh:不撤销在途 Mandate、变更通知到在途工单)。
        if run_repo is not None and audit_repo is not None:
            _note_policy_expiry(name, team, run_repo, audit_repo)
        return {"name": team.name}

    # —— 记忆层: 团队跨 run 记忆的查看与清理 ——

    @router.get("/{name}/memories")
    def list_memories(name: str, limit: int = 20):
        if store.get(name) is None:
            raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
        if memory_repo is None:
            return []
        return memory_repo.list_for_team(name, limit=max(1, min(limit, 200)))

    @router.delete("/{name}/memories/{memory_id}")
    def delete_memory(name: str, memory_id: int):
        if memory_repo is None:
            raise HTTPException(status_code=404, detail="Memory layer not configured")
        if not memory_repo.delete_memory(name, memory_id):
            raise HTTPException(
                status_code=404, detail=f"Memory {memory_id} not found for team '{name}'"
            )
        return {"ok": True}

    return router


def _note_policy_expiry(team_name: str, team, run_repo, audit_repo) -> int:
    """团队热更新后,给进行中 run 的轨迹记 policy 过期注记(SP12 Mandate)。

    只注记不中断:run 用的仍是创建时的授权依据(旧 policy_version),
    新版本只影响之后新提交的 run。返回注记的 run 数。
    """
    from agentteam.domain.mandate import policy_version_for_team

    current_version = policy_version_for_team(team)
    noted = 0
    for row in run_repo.list_active_by_team(team_name):
        raw = row["mandate_json"] if "mandate_json" in row.keys() else None
        if not raw:
            continue  # 旧 run 无授权凭据,无版本可比
        try:
            old_version = json.loads(raw).get("policy_version")
        except (json.JSONDecodeError, TypeError, AttributeError):
            continue
        if old_version and old_version != current_version:
            audit_repo.add_event(
                row["id"], "policy_expired", "system",
                {
                    "run_policy_version": old_version,
                    "current_policy_version": current_version,
                    "note": "团队配置已热更新,该 run 的授权依据已过期(不中断运行)",
                },
                state_bucket="policy",
            )
            noted += 1
    return noted
