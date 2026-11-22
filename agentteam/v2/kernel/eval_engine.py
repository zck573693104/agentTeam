"""Eval 轻量研判(dsh 三事实: 针对本项目/有实施意图/方向已定)。

P5: 可注入「历史校准规则」(人工确认样本蒸馏产物,见 eval_distill.py)——
gate/Eval 随人工确认自我校准。
"""
from __future__ import annotations

from typing import Callable

from pydantic import BaseModel, Field

from agentteam.models.provider import ModelProvider, ModelRef
from agentteam.v2.kernel.gate import Gate


class EvalDecision(BaseModel):
    """三事实研判结果(轻量模型)。"""

    accept: bool = Field(description="三事实是否全部成立")
    reason: str = Field(default="", description="一句话研判理由")


# 三事实(dsh Eval 前提): spec §7.3
EVAL_PROMPT_TEMPLATE = """你是入站事件的研判助手。判断以下入站文本是否满足全部三个事实:
1. 针对本项目(不是闲聊/无关话题);
2. 有实施意图(不是纯咨询/吐槽);
3. 方向已定(不是模糊开放的想法)。

全部成立 → accept=true;任一不成立 → accept=false(拿不准倾向不行动)。
{rules_block}
入站文本:
{text}
"""


class EvalEngine:
    def __init__(
        self, model_provider: ModelProvider, default_model: ModelRef,
        rules_provider: Callable[[], list[str]] | None = None,
    ) -> None:
        self._mp = model_provider
        self._model = default_model
        self._rules_provider = rules_provider  # P5 校准规则源(InboundDistiller);None = 无

    def evaluate(self, text: str) -> EvalDecision:
        llm = self._mp.get_llm(self._model)
        try:
            rules_block = ""
            if self._rules_provider is not None:
                lines = self._rules_provider()
                if lines:
                    rules_block = "【历史校准规则】(人工确认样本蒸馏,满足任一条倾向 accept=true)\n" \
                        + "\n".join(f"- {ln}" for ln in lines) + "\n"
            decision = llm.with_structured_output(EvalDecision).invoke(
                EVAL_PROMPT_TEMPLATE.format(text=text[:2000], rules_block=rules_block),
            )
            return decision
        except Exception:
            # LLM 不可用:拿不准倾向不行动
            return EvalDecision(accept=False, reason="eval llm unavailable")

    @staticmethod
    def gate_from_source_config(config: dict) -> Gate:
        return Gate(config)
