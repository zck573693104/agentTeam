"""Router — 前台大管家(dsh: 接单/派工/统一对外,无执行工具,批注 ≤30 词)。

派工决策两级:
1. 确定性:请求显式指定 worker → 直接派工(不进 LLM);
2. LLM 研判:结构化输出 dispatch|reply;worker 不存在/解析失败 →
   兜底默认 worker(dsh: Router 不做技术决策,这里连"选哪个 worker 的技术判断"
   也不做,只做"谁适合接"的路由判断)。
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from agentteam.logging_config import get_logger
from agentteam.models.provider import ModelProvider, ModelRef
from agentteam.v2.domain.discipline import ROUTER_DISCIPLINE
from agentteam.v2.domain.worker import WorkerDef

logger = get_logger("v2.kernel.router")

_NOTE_MAX_CHARS = 120  # 批注运行时上限(discipline「≤30 词」的硬约束兜底)


class RouterDecision(BaseModel):
    """Router 结构化决策(with_structured_output schema)。"""

    action: str = Field(description="dispatch(派工) 或 reply(直接答复)")
    worker: str | None = Field(default=None, description="dispatch 时的目标 worker 名")
    goal: str = Field(default="", description="派工目标(不含实现方案)")
    acceptance: str = Field(default="", description="验收标准")
    note: str = Field(default="", description="路由批注,不超过 30 词")
    reply: str = Field(default="", description="action=reply 时给用户的直接答复")


@dataclass
class DispatchDecision:
    action: str                 # dispatch | reply
    worker_name: str | None
    goal: str
    acceptance: str
    note: str
    reply: str | None = None
    via: str = "llm"            # llm | explicit | fallback


class Router:
    def __init__(
        self,
        model_provider: ModelProvider,
        default_model: ModelRef,
    ) -> None:
        self._mp = model_provider
        self._default_model = default_model

    def _roster(self, workers: list[WorkerDef]) -> str:
        lines = ["可用 worker(只能派给其中之一):"]
        for w in workers:
            duty = (w.system_prompt or "").strip().splitlines()
            duty_text = duty[0] if duty else "(无职责说明)"
            lines.append(f"- {w.name}: {duty_text[:60]}")
        return "\n".join(lines)

    def decide(
        self,
        text: str,
        workers: list[WorkerDef],
        requested_worker: str | None = None,
    ) -> DispatchDecision:
        enabled = [w for w in workers if w.enabled]
        if not enabled:
            return DispatchDecision(
                action="reply", worker_name=None, goal="", acceptance="",
                note="no worker", reply="当前没有可用的 worker,请先在工人池中添加。",
                via="fallback",
            )

        # 1) 确定性:显式指定 worker(请求者已做了路由决策)
        if requested_worker:
            names = {w.name for w in enabled}
            if requested_worker in names:
                return DispatchDecision(
                    action="dispatch", worker_name=requested_worker,
                    goal=text, acceptance="", note="", via="explicit",
                )
            logger.warning("requested worker %r not found, fallback to llm", requested_worker)

        # 2) LLM 研判
        llm = self._mp.get_llm(self._default_model)
        prompt = (
            f"{ROUTER_DISCIPLINE}\n\n{self._roster(enabled)}\n\n"
            f"用户请求:\n{text}\n\n"
            "请决定:派工(dispatch)还是直接答复(reply)。"
        )
        try:
            decision = llm.with_structured_output(RouterDecision).invoke(prompt)
        except Exception as exc:
            logger.warning("router llm failed (%s), fallback to default worker", exc)
            decision = None

        if decision is not None and getattr(decision, "action", None) == "dispatch" \
                and getattr(decision, "worker", None):
            names = {w.name for w in enabled}
            if decision.worker in names:
                return DispatchDecision(
                    action="dispatch", worker_name=decision.worker,
                    goal=decision.goal or text, acceptance=decision.acceptance,
                    note=(decision.note or "")[:_NOTE_MAX_CHARS], via="llm",
                )
            logger.warning("router picked unknown worker %r, fallback", decision.worker)

        # 3) 兜底:reply 有值则答复,否则派给第一个 worker
        if decision is not None and getattr(decision, "action", None) == "reply" \
                and getattr(decision, "reply", ""):
            return DispatchDecision(
                action="reply", worker_name=None, goal="", acceptance="",
                note=(decision.note or "")[:_NOTE_MAX_CHARS], reply=decision.reply, via="llm",
            )
        fallback = enabled[0]
        return DispatchDecision(
            action="dispatch", worker_name=fallback.name,
            goal=text, acceptance="", note="router fallback",
            via="fallback",
        )
