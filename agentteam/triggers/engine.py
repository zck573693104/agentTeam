"""TriggerEngine:入站事件 → 两级过滤 → 创建 run(SP12c)。

决策结果统一为 dict:
{"matched": bool, "run_id": str|None, "via": "fast_path"|"slow_path"|None,
 "reason": str|None, "status_code": int|None}

- matched=True 且 run_id 非 None:已创建 run
- matched=True 且 status_code 非 None:命中但提交失败(配额/底座),透传给调用方
- matched=False:被 Fast Path 过滤 / Slow Path 拒绝 / Slow Path 不可用
"""
from __future__ import annotations

import json
import logging
import re
from typing import Callable

from agentteam.api.routes.runs import RunSubmitError
from agentteam.config import get_settings
from agentteam.domain.trigger import TriggerRule, resolve_path
from agentteam.models.provider import ModelProvider, ModelRef
from agentteam.storage.triggers import TriggerRepo

logger = logging.getLogger(__name__)

# 喂给 Slow Path 判定器的事件 JSON 上限
_EVENT_JSON_MAX = 6000

RunStarter = Callable[[str, str, str], str]


class TriggerEngine:
    """两级过滤 + 命中后创建 run。异常一律静默隔离,绝不抛给入站路径之外。"""

    def __init__(
        self,
        trigger_repo: TriggerRepo,
        model_provider: ModelProvider,
        run_starter: RunStarter,
        audit_repo=None,
        default_model: ModelRef | None = None,
    ) -> None:
        self._repo = trigger_repo
        self._mp = model_provider
        self._run_starter = run_starter
        self._audit = audit_repo
        self._default_model = default_model or ModelRef("qwen", "qwen-max")

    def handle_event(self, rule_name: str, event: dict) -> dict:
        try:
            rule = self._repo.get_by_name(rule_name)
            if rule is None:
                return {"matched": False, "run_id": None, "via": None,
                        "reason": "rule_not_found", "status_code": None}
            if not rule.enabled:
                return {"matched": False, "run_id": None, "via": None,
                        "reason": "rule_disabled", "status_code": None}

            # 两级语义:
            # - 纯 Fast Path 规则(未开 slow_path):条件全命中才行动,空条件=全放行
            # - Slow Path 规则:conditions 是「候选预过滤」——命中(或未配条件)
            #   的事件全部交 LLM 语义判定,由判定结果决定是否行动
            #   (DeepSeek 3.1:确定性代码滤噪音,语义理解交模型)
            if rule.slow_path_enabled():
                if rule.conditions and not rule.matches_fast_path(event):
                    return {"matched": False, "run_id": None, "via": None,
                            "reason": "filtered_by_fast_path", "status_code": None}
                decision = self._slow_path_judge(rule, event)
                if decision is None:
                    return {"matched": False, "run_id": None, "via": None,
                            "reason": "slow_path_unavailable", "status_code": None}
                act, reason = decision
                if not act:
                    return {"matched": False, "run_id": None, "via": None,
                            "reason": f"slow_path_declined: {reason}", "status_code": None}
                return self._fire(rule, event, via="slow_path", reason=reason)

            if rule.matches_fast_path(event):
                return self._fire(rule, event, via="fast_path", reason=None)
            return {"matched": False, "run_id": None, "via": None,
                    "reason": "filtered_by_fast_path", "status_code": None}
        except Exception:
            logger.exception("trigger %s handling failed", rule_name)
            return {"matched": False, "run_id": None, "via": None,
                    "reason": "engine_error", "status_code": None}

    # ---------- 内部 ----------

    def _fire(self, rule: TriggerRule, event: dict, via: str, reason: str | None) -> dict:
        task = rule.build_task(event)
        if not task:
            return {"matched": False, "run_id": None, "via": None,
                    "reason": "task_text_empty", "status_code": None}
        try:
            run_id = self._run_starter(
                rule.team_name, task, triggered_by_user=f"trigger:{rule.name}",
            )
        except RunSubmitError as e:
            # 命中但提交失败(配额/底座)——透传业务状态码给调用方
            return {"matched": True, "run_id": None, "via": via, "reason": reason,
                    "error": str(e), "status_code": e.status_code}
        if self._audit is not None:
            try:
                self._audit.add_event(
                    run_id, "trigger_fired", "system",
                    {"rule": rule.name, "via": via, "reason": reason,
                     "source": event.get("source")},
                )
            except Exception:
                logger.warning("trigger_fired audit write failed for run %s", run_id)
        return {"matched": True, "run_id": run_id, "via": via,
                "reason": reason, "status_code": None}

    def _slow_path_judge(self, rule: TriggerRule, event: dict) -> tuple[bool, str] | None:
        """LLM 语义判定。任何不可用/异常都返回 None(按 Fast Path 未命中处理)。"""
        settings = get_settings()
        if not settings.triggers_slow_path_enabled:
            return None
        if not rule.slow_path_enabled():
            return None
        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            from agentteam.triggers.prompts import TRIGGER_SLOW_PATH_INSTRUCTION

            llm = self._mp.get_llm(self._default_model)
            event_json = json.dumps(event, ensure_ascii=False, default=str)[:_EVENT_JSON_MAX]
            strategy = rule.slow_path_prompt() or "(无附加策略,仅在事件明确是给 Agent 的任务请求时行动)"
            response = llm.invoke([
                SystemMessage(content=TRIGGER_SLOW_PATH_INSTRUCTION),
                HumanMessage(content=(
                    f"判定策略:\n{strategy}\n\n"
                    f"环境事件:\n{event_json}\n\n请判断是否应该行动。"
                )),
            ])
            return _parse_decision(response.content)
        except Exception:
            logger.exception("slow path judge failed for rule %s", rule.name)
            return None


def _parse_decision(response: str) -> tuple[bool, str] | None:
    """从 LLM 响应提取 {"act": bool, "reason": str};解析失败返回 None。"""
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", response, re.DOTALL)
    raw = match.group(1) if match else response
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("act"), bool):
        return None
    return data["act"], str(data.get("reason") or "")


def check_secret(rule: TriggerRule, headers: dict) -> bool:
    """入站 secret 校验:规则未配置 secret 则放行;配置了则必须头匹配。"""
    if not rule.secret:
        return True
    return headers.get("x-trigger-secret") == rule.secret


__all__ = ["TriggerEngine", "check_secret", "resolve_path"]
