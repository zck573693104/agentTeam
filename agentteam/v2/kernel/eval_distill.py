"""InboundDistiller — 观察模式确认信号的蒸馏回路(P5 housekeeping)。

dsh 观察模式账本里的 confirm(人工确认 would_accept 转正)是最强正信号:
「这类请求应该立项」。本蒸馏器积累确认样本,攒够阈值后由 sleep-time 角色
(postmortem housekeeping)异步蒸馏为「入站研判校准规则」,回灌 EvalEngine ——
gate/Eval 从此随人工确认自我校准,而不是静态规则。

文件形态(OpenViking 范式,人可审可改):
- inbound/eval_samples.json  待蒸馏样本(确认的入站文本,环形上限)
- inbound/eval_rules.md      已蒸馏规则(一行一条,Eval prompt 注入源)
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from pydantic import BaseModel, Field

from agentteam.logging_config import get_logger
from agentteam.models.provider import ModelProvider, ModelRef

logger = get_logger("v2.kernel.eval_distill")

_MAX_SAMPLES = 50
_MAX_RULES = 20
_MAX_RULE_LINE = 120
_DISTILL_PROMPT = """你是入站研判校准器。以下是人工确认「应立项」的入站请求样本:
提炼「什么样的入站请求应被接受」的可执行判断规则(是对三事实的细化,不是重复):
- 只写能区分该不该接受的具体特征(动作动词/对象/粒度);
- 最多 {max_rules} 条,每条一句话;
- 宁缺毋滥,没有共性就少写。

样本:
{samples}
"""


class EvalRules(BaseModel):
    """蒸馏产出的校准规则。"""

    rules: list[str] = Field(default_factory=list, description="一条一条的判断规则")


class InboundDistiller:
    """确认样本积累 + 异步蒸馏 + 规则文件管理(线程安全)。"""

    def __init__(
        self,
        root: Path | None,
        model_provider: ModelProvider,
        aux_model: ModelRef,
        threshold: int = 5,
    ) -> None:
        self._root = root          # memory/inbound 目录;None = 停用
        self._mp = model_provider
        self._aux_model = aux_model
        self._threshold = threshold
        self._lock = threading.Lock()
        self._rules_cache: list[str] | None = None
        self._rules_mtime: float = 0.0

    # —— 样本积累(confirm 时同步调用,毫秒级) ——
    def record_confirm(self, text: str) -> int:
        """记录一条人工确认样本;返回当前待蒸馏样本数。root 为 None → 永远 0。"""
        if self._root is None:
            return 0
        clean = (text or "").strip()[:500]
        if not clean:
            return 0
        with self._lock:
            samples = self._read_samples()
            samples.append(clean)
            samples = samples[-_MAX_SAMPLES:]
            self._write_samples(samples)
            return len(samples)

    def pending_count(self) -> int:
        if self._root is None:
            return 0
        with self._lock:
            return len(self._read_samples())

    # —— 蒸馏(housekeeping 线程内调用) ——
    def maybe_distill(self) -> list[str] | None:
        """样本攒够阈值 → 蒸馏为新规则(合并旧规则)。未达阈值返回 None。"""
        if self._root is None or self._threshold <= 0:
            return None
        with self._lock:
            samples = self._read_samples()
            if len(samples) < self._threshold:
                return None
            self._write_samples([])  # 先取走,防蒸馏失败反复重试同一批
        prompt = _DISTILL_PROMPT.format(
            max_rules=_MAX_RULES,
            samples="\n".join(f"- {s}" for s in samples) or "(空)",
        )
        try:
            llm = self._mp.get_llm(self._aux_model)
            result = llm.with_structured_output(EvalRules).invoke(prompt)
            rules = result.rules if isinstance(result, EvalRules) else EvalRules(**result).rules
        except Exception as exc:
            logger.warning("eval rules distill failed: %s", exc)
            return None
        cleaned = []
        for r in rules:
            line = re.sub(r"\s+", " ", str(r)).strip()[:_MAX_RULE_LINE]
            if line and line not in cleaned:
                cleaned.append(line)
        if not cleaned:
            return []
        merged = [r for r in self.lines() if r not in cleaned][-(_MAX_RULES - len(cleaned)):]
        merged = merged + cleaned
        self._write_rules(merged)
        logger.info("eval rules distilled: +%d (total %d)", len(cleaned), len(merged))
        return cleaned

    # —— 规则读取(EvalEngine 注入源,mtime 缓存) ——
    def lines(self, limit: int = 8) -> list[str]:
        if self._root is None:
            return []
        path = self._root / "eval_rules.md"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return []
        with self._lock:
            if self._rules_cache is not None and mtime == self._rules_mtime:
                return self._rules_cache[:limit]
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError:
                return []
            rules = [ln.strip()[2:] for ln in raw.splitlines() if ln.startswith("- ")]
            self._rules_cache, self._rules_mtime = rules, mtime
            return rules[:limit]

    # —— 文件原子读写 ——
    def _read_samples(self) -> list[str]:
        path = self._root / "eval_samples.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return []

    def _write_samples(self, samples: list[str]) -> None:
        self._atomic_write(self._root / "eval_samples.json",
                           json.dumps(samples, ensure_ascii=False))

    def _write_rules(self, rules: list[str]) -> None:
        rules = rules[:_MAX_RULES]
        header = "# 入站研判校准规则(L0)\n\n源自人工确认样本蒸馏;EvalEngine 注入;可手改。\n"
        self._atomic_write(self._root / "eval_rules.md",
                           header + ("\n".join(f"- {r}" for r in rules) + "\n" if rules else ""))
        with self._lock:
            self._rules_cache = None  # 失效缓存

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
