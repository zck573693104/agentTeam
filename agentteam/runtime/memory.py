"""MemoryEngine:Run 终态后异步蒸馏团队级跨 run 记忆。

设计原则(与 SP7b EvolutionEngine 同纪律):
- 异步:RunManager 终态分支的 daemon 线程触发,不阻塞主流程
- 隔离:蒸馏失败仅记日志,绝不影响 run 结果
- 全团队生效:挂在 RunManager 终态收敛点,所有 langgraph 团队都覆盖
- 团队级:记忆按 team_name 聚合(非 agent 级),注入下一次同团队 run 的
  leader 上下文,让团队「记得」上次做过什么、踩过什么坑

注入侧 build_memory_context 也在这里,供 run_manager 构造初始 state 使用。
"""
from __future__ import annotations

import json
import logging
import re

from agentteam.config import get_settings
from agentteam.models.provider import ModelProvider, ModelRef
from agentteam.storage.audit import AuditRepo
from agentteam.storage.memory import MemoryRepo
from agentteam.storage.runs import RunRepo

logger = logging.getLogger(__name__)

# 每条记忆格式化时的摘要上限,防单条失控挤占注入预算
_SUMMARY_MAX_CHARS = 500


class MemoryEngine:
    """run 终态 → LLM 蒸馏 → run_memories 落库。"""

    def __init__(
        self,
        model_provider: ModelProvider,
        memory_repo: MemoryRepo,
        run_repo: RunRepo,
        audit_repo: AuditRepo,
        default_model: ModelRef,
    ) -> None:
        self._mp = model_provider
        self._memory_repo = memory_repo
        self._run_repo = run_repo
        self._audit = audit_repo
        self._default_model = default_model

    def trigger(self, run_id: str) -> None:
        """RunManager 在 run 终态(completed/failed)后异步调用。

        任何异常都被吞掉(log 后返回)——记忆是增值能力,不能拖垮主流程。
        """
        try:
            settings = get_settings()
            if not settings.memory_enabled:
                return
            run = self._run_repo.get_run(run_id)
            if run is None:
                return
            team_name = run["team_name"]
            if not team_name:
                return
            outcome = run["status"]

            trace = self._load_trace(run_id)
            if not trace:
                return

            distilled = self._distill(run_id, team_name, outcome, trace)
            if distilled is None:
                return
            summary, meta, tokens = distilled

            memory_id = self._memory_repo.add_memory(
                team_name=team_name,
                run_id=run_id,
                outcome=outcome,
                summary=summary,
                meta=meta,
                tokens=tokens,
            )
            # 不写 run_events(与 EvolutionEngine 同纪律):run_end 之后追加事件
            # 会破坏 SSE「run_end 关流」语义;记忆可经 teams/{name}/memories 查询
            logger.info(
                "memory #%s distilled for team %s from run %s (outcome=%s, tokens=%d)",
                memory_id, team_name, run_id, outcome, tokens,
            )
        except Exception:
            logger.exception("memory distillation failed for run %s", run_id)

    def recent_memories(self, team_name: str, limit: int = 8) -> list[dict]:
        """读取团队最近记忆(供 RunManager 注入初始 state 时委托调用)。"""
        try:
            return self._memory_repo.list_for_team(team_name, limit=limit)
        except Exception:
            logger.exception("list memories failed for team %s", team_name)
            return []

    def _load_trace(self, run_id: str) -> list[dict]:
        """加载 run 的 audit_events 并统一转为 dict。"""
        try:
            raw_events = self._audit.list_events(run_id)
        except Exception:
            return []
        return [dict(ev) if not isinstance(ev, dict) else ev for ev in raw_events]

    def _distill(
        self, run_id: str, team_name: str, outcome: str, trace: list[dict]
    ) -> tuple[str, dict | None, int] | None:
        """一次 LLM 调用蒸馏。返回 (summary, meta, tokens) 或 None(无可提炼)。"""
        from langchain_core.messages import HumanMessage, SystemMessage

        from agentteam.runtime.evolution import _extract_task, _summarize_trace
        from agentteam.runtime.memory_prompts import MEMORY_DISTILLER_INSTRUCTION

        task = _extract_task(trace)
        trace_summary = _summarize_trace(trace)

        llm = self._mp.get_llm(self._default_model)
        response = llm.invoke([
            SystemMessage(content=MEMORY_DISTILLER_INSTRUCTION),
            HumanMessage(content=(
                f"团队: {team_name}\n"
                f"任务: {task}\n"
                f"结果: {outcome}\n\n"
                f"执行 trace:\n{trace_summary}\n\n"
                f"请提炼本次运行的记忆。"
            )),
        ])
        tokens = 0
        usage = getattr(response, "usage_metadata", None)
        if isinstance(usage, dict):
            tokens = usage.get("total_tokens", 0) or 0
        return _parse_memory_response(response.content)


def _parse_memory_response(response: str) -> tuple[str, dict | None, int] | None:
    """从 LLM 响应提取记忆 JSON;解析失败退化为原文摘要。

    返回 (summary, meta, tokens=0)。meta 携带 key_points/files 结构化字段。
    """
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", response, re.DOTALL)
    raw = match.group(1) if match else response
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # JSON 解析失败:整个响应作为 summary,不丢记忆
        text = response.strip()
        return (text[:_SUMMARY_MAX_CHARS] or "无可提炼内容", None, 0) if text else None
    if not isinstance(data, dict):
        return None

    summary = str(data.get("summary") or "").strip()
    key_points = [str(p) for p in (data.get("key_points") or []) if str(p).strip()]
    files = [str(f) for f in (data.get("files") or []) if str(f).strip()]
    if not summary and not key_points:
        return None
    if not summary:
        summary = "；".join(key_points)
    meta: dict = {}
    if key_points:
        meta["key_points"] = key_points
    if files:
        meta["files"] = files
    return summary[:_SUMMARY_MAX_CHARS], meta or None, 0


def build_memory_context(memories: list[dict], budget_tokens: int) -> str:
    """把记忆列表格式化为注入 leader 上下文的 <memory> 块。

    memories 按 list_for_team 的顺序(新→旧)消费,超预算即止
    (token 用 chars/4 估算,项目无 tokenizer,与 budget_tokens 的
    警告级语义一致)。至少保留最新一条,避免预算过小导致注入恒为空。
    返回空串表示不注入。
    """
    if not memories or budget_tokens <= 0:
        return ""
    budget_chars = budget_tokens * 4
    lines: list[str] = []
    used = 0
    for mem in memories:
        summary = str(mem.get("summary") or "").strip().replace("\n", "；")
        if not summary:
            continue
        summary = summary[:_SUMMARY_MAX_CHARS]
        line = (
            f"- [{(mem.get('created_at') or '')[:16]} · {mem.get('outcome', '?')}] "
            f"{summary}"
        )
        if lines and used + len(line) > budget_chars:
            break
        lines.append(line)
        used += len(line) + 1
    if not lines:
        return ""
    header = f'<memory source="团队历史记忆(最近 {len(lines)} 次运行)">'
    return header + "\n" + "\n".join(lines) + "\n</memory>"
