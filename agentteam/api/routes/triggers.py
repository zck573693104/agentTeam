"""SP12c: 事件驱动 Trigger 路由。

规则 CRUD(/api/triggers)+ 入站 webhook(/api/triggers/webhook/{name})。
入站端点 = DeepSeek Super-agent 事件驱动里的 Trigger 入口:
secret 校验(Fast Path 前置) → 两级过滤 → 命中走与人工提交相同的 run 流程。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from agentteam.api.routes.runs import RunSubmitError
from agentteam.domain.trigger import TriggerRule
from agentteam.triggers.engine import TriggerEngine, check_secret
from agentteam.api.store import TeamStore
from agentteam.storage.triggers import TriggerRepo


def triggers_router(
    trigger_repo: TriggerRepo,
    trigger_engine: TriggerEngine,
    team_store: TeamStore,
) -> APIRouter:
    router = APIRouter(prefix="/api/triggers", tags=["triggers"])

    def _require_team(rule: TriggerRule) -> None:
        if team_store.get(rule.team_name) is None:
            raise HTTPException(
                status_code=422,
                detail=f"Team '{rule.team_name}' not found (register it first)",
            )

    @router.post("")
    def register_trigger(body: dict):
        try:
            rule = TriggerRule.from_dict(body)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"Invalid trigger rule: {e}")
        _require_team(rule)
        try:
            trigger_repo.add_rule(rule)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"name": rule.name}

    @router.get("")
    def list_triggers(team_name: str | None = None):
        return [r.to_dict(mask_secret=True) for r in trigger_repo.list_rules(team_name)]

    @router.get("/{name}")
    def get_trigger(name: str):
        rule = trigger_repo.get_by_name(name)
        if rule is None:
            raise HTTPException(status_code=404, detail=f"Trigger rule '{name}' not found")
        return rule.to_dict(mask_secret=True)

    @router.put("/{name}")
    def update_trigger(name: str, body: dict):
        if trigger_repo.get_by_name(name) is None:
            raise HTTPException(status_code=404, detail=f"Trigger rule '{name}' not found")
        body = dict(body or {})
        body["name"] = name  # 路径名优先,防 body 改名造成错位
        try:
            rule = TriggerRule.from_dict(body)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"Invalid trigger rule: {e}")
        _require_team(rule)
        trigger_repo.update_rule(rule)
        return {"name": name}

    @router.delete("/{name}")
    def delete_trigger(name: str):
        if not trigger_repo.delete_rule(name):
            raise HTTPException(status_code=404, detail=f"Trigger rule '{name}' not found")
        return {"ok": True}

    @router.post("/webhook/{name}")
    async def inbound_webhook(name: str, request: Request):
        """入站事件端点:任何能 POST JSON 的系统(GitHub/IM bot/cron/自建)都是事件源。

        - 规则配了 secret → 请求头 X-Trigger-Secret 必须匹配,否则 401
        - 两级过滤:Fast Path 确定性条件 / Slow Path LLM 判定
        - 命中 → 与人工 POST /api/runs 完全相同的提交流程(配额/审计)
        """
        rule = trigger_repo.get_by_name(name)
        if rule is None:
            raise HTTPException(status_code=404, detail=f"Trigger rule '{name}' not found")
        headers = {k.lower(): v for k, v in request.headers.items()}
        if not check_secret(rule, headers):
            raise HTTPException(status_code=401, detail="Invalid trigger secret")
        try:
            payload: Any = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {"raw": payload}
        event = {
            "source": rule.source,
            "headers": headers,
            "payload": payload,
            "text": str(payload.get("text") or payload.get("message") or ""),
        }
        decision = trigger_engine.handle_event(name, event)
        if decision["matched"] and decision.get("status_code"):
            # 命中但 run 提交失败(配额/编译失败)——透传业务状态码
            raise HTTPException(
                status_code=decision["status_code"],
                detail=f"trigger '{name}' matched but run submission failed",
            )
        return decision

    return router
