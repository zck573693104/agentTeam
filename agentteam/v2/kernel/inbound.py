"""InboundEngine — 入站事件处理管线(dsh SP12e): gate 快筛 → Eval 研判 → 立项/观察。

dsh 语义:
- Trigger 不可变(含 actor),Thread 是 Eval 的上下文单元;
- 观察模式默认:inferred 立项先只记录 would-accept,人工确认后才转正;
- 拿不准倾向不行动(Eval 异常/不可用 → 不立项);
- 外部文本是数据不是规则:入站文本不改变授权。
"""
from __future__ import annotations

import json

from agentteam.logging_config import get_logger
from agentteam.v2.domain import events as ev
from agentteam.v2.kernel.eval_engine import EvalEngine
from agentteam.v2.kernel.gate import Gate
from agentteam.v2.sources.webhook import Trigger
from agentteam.v2.storage.events import EventBus, EventRepo
from agentteam.v2.storage.sources import EvaluationRepo, SourceRepo
from agentteam.v2.storage.threads import ThreadRepo

logger = get_logger("v2.kernel.inbound")


class InboundEngine:
    def __init__(
        self,
        *,
        source_repo: SourceRepo,
        thread_repo: ThreadRepo,
        evaluation_repo: EvaluationRepo,
        eval_engine: EvalEngine,
        event_repo: EventRepo,
        bus: EventBus,
        submit_from_source,  # Scheduler.submit_request 的 inferred 封装
        fanout=None,         # Scheduler.fan_out_thread(A4 双向 pass-through)
    ) -> None:
        self.sources = source_repo
        self.threads = thread_repo
        self.evaluations = evaluation_repo
        self.eval_engine = eval_engine
        self.events = event_repo
        self.bus = bus
        self._submit = submit_from_source
        self._fanout = fanout

    def handle(self, source_name: str, trigger: Trigger) -> dict:
        """入站事件 → gate → Eval → (auto 立项 | would_accept 观察 | rejected)。

        返回 {decision, reason, thread_id, trigger_id, evaluation_id, ticket_id?}。
        """
        src = self.sources.get(source_name)
        if src is None or not src["enabled"]:
            raise KeyError(f"source '{source_name}' not found or disabled")

        thread = self.threads.get_or_create(source_name, trigger.thread_key)
        trig = self.threads.append_trigger(
            thread["id"], trigger.actor, trigger.text, raw=trigger.raw,
        )
        # A4 双向 pass-through: 同线程活跃工单的 worker 直接收到此事件
        # (独立于 gate/Eval 流程;终态工单自动跳过)
        if self._fanout is not None:
            self._fanout(thread["id"], trigger.text, trigger.actor)

        # —— gate 程序快筛(毫秒级,不进 LLM) ——
        gate = Gate(src["config"])
        passed, reason = gate.check(trigger.actor, trigger.text)
        if not passed:
            evaluation = self.evaluations.create(
                trig["id"], {"passed": False, "reason": reason}, decision="rejected",
                reason=reason,
            )
            return {"decision": "rejected", "reason": reason,
                    "thread_id": thread["id"], "trigger_id": trig["id"],
                    "evaluation_id": evaluation["id"]}

        # —— Eval 三事实(轻量模型) ——
        decision = self.eval_engine.evaluate(trigger.text)

        if not decision.accept:
            evaluation = self.evaluations.create(
                trig["id"], {"passed": True, "reason": reason}, decision="rejected",
                reason=decision.reason or "三事实不成立",
            )
            return {"decision": "rejected", "reason": decision.reason,
                    "thread_id": thread["id"], "trigger_id": trig["id"],
                    "evaluation_id": evaluation["id"]}

        # —— 观察模式默认(dsh: 主动行为先记录不执行) ——
        mode = (src["config"] or {}).get("mode", "observe")
        if mode == "observe":
            evaluation = self.evaluations.create(
                trig["id"], {"passed": True, "reason": reason}, decision="would_accept",
                reason=decision.reason,
            )
            return {"decision": "would_accept", "reason": decision.reason,
                    "thread_id": thread["id"], "trigger_id": trig["id"],
                    "evaluation_id": evaluation["id"]}

        # —— auto: 直接立项(Mandate.basis=inferred) ——
        return self._promote(src, thread["id"], trig["id"], decision.reason,
                             confirmed_by=f"source:{source_name}",
                             eval_id=None)

    def confirm(self, evaluation_id: str, confirmed_by: str = "human") -> dict:
        """观察模式转正:would_accept → 立项(人工确认后 switch to auto)。"""
        evaluation = self.evaluations.get(evaluation_id)
        if evaluation is None:
            raise KeyError(f"evaluation '{evaluation_id}' not found")
        if evaluation["decision"] != "would_accept":
            raise RuntimeError(f"evaluation is {evaluation['decision']}, not would_accept")
        trigger = self._get_trigger(evaluation["trigger_id"])
        if trigger is None:
            raise RuntimeError("trigger gone")
        thread = self.threads.get(trigger["thread_id"])
        src = self.sources.get(thread["source"]) if thread else None
        if src is None:
            raise RuntimeError("source gone")
        return self._promote(
            src, trigger["thread_id"], trigger["id"],
            evaluation["reason"] or "人工确认转正", confirmed_by=confirmed_by,
            eval_id=evaluation_id,
        )

    def _promote(self, src: dict, thread_id: str, trigger_id: str,
                 reason: str, confirmed_by: str, eval_id: str | None) -> dict:
        """立项:Router 派工 + inferred Mandate + thread 绑定。

        eval_id 非空(观察转正)时翻转原 would_accept 记录;auto 模式新建 accepted。
        """
        trigger = self._get_trigger(trigger_id)
        result = self._submit(
            text=trigger["text"],
            source_name=src["name"],
            thread_id=thread_id,
        )
        if eval_id:
            self.evaluations.set_ticket(eval_id, result["ticket_id"], decision="accepted")
            evaluation = self.evaluations.get(eval_id)
        else:
            evaluation = self.evaluations.create(
                trigger_id, {"passed": True, "reason": "passed"}, decision="accepted",
                reason=reason, ticket_id=result["ticket_id"],
            )
        return {
            "decision": "accepted",
            "reason": reason,
            "thread_id": thread_id,
            "trigger_id": trigger_id,
            "evaluation_id": evaluation["id"],
            "ticket_id": result["ticket_id"],
            "worker": result.get("worker"),
            "confirmed_by": confirmed_by,
        }

    def _get_trigger(self, trigger_id: str) -> dict | None:
        row = self.threads._fetchone(
            "SELECT * FROM triggers WHERE id = ?", (trigger_id,),
        )
        if row is None:
            return None
        d = dict(row)
        d["raw"] = json.loads(d.pop("raw_json") or "{}")
        return d
