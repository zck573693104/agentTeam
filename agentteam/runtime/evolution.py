"""EvolutionEngine:Run 终态后异步触发 4 维度自进化。

4 维度:PromptOptimizer / ParamTuner / SkillGenerator / SkillSelector。
设计原则:
- 异步:不阻塞 RunManager 的 API 响应
- 隔离:4 维度独立 LLM 调用 + 独立写 history,互不影响
- 失败保护:任一维度失败仅记 error,不影响其他维度 / run 结果
- 防抖:同一 agent 5 分钟内只触发一次
- 版本原子性:一次 trigger 内 4 维度全部尝试后,任一成功则 version += 1

Graph Engineering P6 反指标(防止进化优化到偏离初衷):
文章原文:"防止指标被优化到偏离初衷:加反指标(anti_metrics),
冻结不变量(frozen_rules),提案需通过反指标验证。"
- anti_metrics: 不应被优化目标的指标列表(如安全/合规相关),
  记录到 history.reason 供人工 review,提醒该次进化可能偏离初衷。
- frozen_rules: 硬约束校验函数列表,提案应用前必须全部通过,
  任一失败则拒绝应用(success=False,reason 记录违规详情)。
"""
from __future__ import annotations

import difflib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agentteam.domain.agent import Agent
from agentteam.domain.library import AgentLibrary
from agentteam.logging_config import get_logger
from agentteam.models.provider import ModelProvider, ModelRef
from agentteam.runtime.skills import SkillLoader
from agentteam.storage.audit import AuditRepo
from agentteam.storage.evolution import EvolutionRepo
from agentteam.storage.runs import RunRepo

logger = get_logger("runtime.evolution")


# Graph Engineering P6: frozen_rule 校验函数签名
# (agent, dimension, before_value, after_value) -> (ok, reason)
# - ok=True: 校验通过,提案可应用
# - ok=False: 校验失败,reason 记录违规详情,提案被拒绝
FrozenRule = Callable[[Agent, str, Any, Any], "tuple[bool, str]"]


def _frozen_max_iterations_range(
    agent: Agent, dimension: str, before: Any, after: Any,
) -> tuple[bool, str]:
    """默认 frozen_rule:max_iterations 必须保持在 [1, 20] 区间。

    防止 ParamTuner 把 max_iterations 调到极端值(如 0 或 1000):
    - 0 会导致 worker 一次都不执行
    - 1000 会导致单次 run 成本失控
    """
    if dimension != "params":
        return True, ""
    if not isinstance(after, dict):
        return True, ""
    mi = after.get("max_iterations")
    if mi is None:
        return True, ""
    try:
        mi_int = int(mi)
    except (TypeError, ValueError):
        return False, f"max_iterations not numeric: {mi!r}"
    if not (1 <= mi_int <= 20):
        return False, f"max_iterations {mi_int} out of [1, 20]"
    return True, ""


@dataclass
class EvolutionConfig:
    """Graph Engineering P6: 进化治理配置。

    anti_metrics: 不应被优化目标的指标名列表(如 "safety_score"/"compliance_rate")。
        evolution_history.reason 会标注这些指标,提醒人工 review 该次进化是否
        以牺牲反指标为代价换取正向指标提升。本字段为软约束(仅记录不阻断),
        因为反指标的实际值需外部 metrics 系统采集,引擎内无法直接测量。
    frozen_rules: 硬约束校验函数列表。每次进化提案应用前必须全部通过,
        任一返回 (False, reason) 则拒绝应用并记 history(success=False)。
        默认包含 _frozen_max_iterations_range。
    """
    anti_metrics: list[str] = field(default_factory=list)
    frozen_rules: list[FrozenRule] = field(default_factory=lambda: [_frozen_max_iterations_range])

    def validate_proposal(
        self, agent: Agent, dimension: str, before: Any, after: Any,
    ) -> tuple[bool, str]:
        """运行所有 frozen_rule,返回 (是否通过, 拒绝原因)。

        任一 frozen_rule 返回 False 即整体失败(短路求值)。
        全部通过返回 (True, "")。
        """
        for rule in self.frozen_rules:
            try:
                ok, reason = rule(agent, dimension, before, after)
            except Exception as e:
                # 校验函数自身异常:保守拒绝,避免异常规则放行危险变更
                return False, f"frozen_rule {rule.__name__} raised: {e}"
            if not ok:
                return False, reason or f"frozen_rule {getattr(rule, '__name__', rule)} rejected"
        return True, ""

    def anti_metric_warning(self, dimension: str) -> str:
        """生成反指标提醒文本,附加到 history.reason。

        无 anti_metrics 时返回空串(不影响现有 reason)。
        """
        if not self.anti_metrics:
            return ""
        return (
            f" [anti-metrics watch: {', '.join(self.anti_metrics)}] "
            f"请人工确认本次 {dimension} 进化未以牺牲反指标为代价。"
        )


@dataclass
class EvolutionResult:
    """单个维度进化结果。"""
    success: bool
    dimension: str
    reason: str
    error: str | None = None


class EvolutionEngine:
    """协调 4 维度进化的主控。"""

    DEBOUNCE_SECONDS = 300  # 5 分钟

    def __init__(
        self,
        model_provider: ModelProvider,
        agent_library: AgentLibrary,
        evolution_repo: EvolutionRepo,
        run_repo: RunRepo,
        audit_repo: AuditRepo,
        default_model: ModelRef,
        skill_loader: SkillLoader | None = None,
        skills_dir: Path | None = None,
        evolution_config: EvolutionConfig | None = None,
    ) -> None:
        self._mp = model_provider
        self._agent_library = agent_library
        self._evolution_repo = evolution_repo
        self._run_repo = run_repo
        self._audit = audit_repo
        self._default_model = default_model
        self._skill_loader = skill_loader
        self._skills_dir = skills_dir
        # Graph Engineering P6: 进化治理配置(反指标 + 冻结不变量)。
        # None 时用默认 config(仅含 max_iterations 范围校验)。
        self._config = evolution_config or EvolutionConfig()
        self._last_trigger: dict[str, float] = {}
        self._lock = threading.Lock()

    def trigger(self, run_id: str) -> None:
        """RunManager 在 run 终态后异步调用。"""
        run = self._run_repo.get_run(run_id)
        if run is None:
            return
        # 取 trace 一次,传给 _collect_agents_from_trace 和 _evolve_agent,
        # 避免 N+1 次重复 DB 查询,并保证两者看到一致的 trace 快照。
        trace = self._load_trace(run_id)
        agents = self._collect_agents_from_trace(trace)
        if not agents:
            return
        for agent_name in agents:
            self._evolve_agent(agent_name, run_id, trace)

    def _load_trace(self, run_id: str) -> list[dict]:
        """加载 run 的 audit_events 并统一转为 dict(生产 sqlite3.Row → dict)。"""
        try:
            raw_events = self._audit.list_events(run_id)
        except Exception:
            logger.exception("failed to load trace for run %s", run_id)
            return []
        return [dict(ev) if not isinstance(ev, dict) else ev for ev in raw_events]

    def _collect_agents_from_trace(self, trace: list[dict]) -> list[str]:
        """从 trace 提取涉及的 agent 名(去重)。

        扫描 worker_start / leader_plan 事件的 actor 字段。
        """
        names: list[str] = []
        seen: set[str] = set()
        for ev in trace:
            ev_type = ev.get("event_type", "")
            if ev_type in ("worker_start", "leader_plan"):
                actor = ev.get("actor", "")
                if actor and actor not in seen:
                    seen.add(actor)
                    names.append(actor)
        return names

    def _evolve_agent(self, agent_name: str, run_id: str, trace: list[dict]) -> None:
        """对单个 agent 执行 4 维度进化。"""
        # 防抖
        now = time.time()
        with self._lock:
            last = self._last_trigger.get(agent_name, 0)
            if now - last < self.DEBOUNCE_SECONDS:
                return
            self._last_trigger[agent_name] = now

        agent = self._agent_library.get(agent_name)
        if agent is None:
            return

        old_version = agent.version

        # 4 维度顺序执行(每个维度独立 try/except,失败仅记 error)
        results: list[EvolutionResult] = []
        results.append(self._optimize_prompt(agent, trace, run_id))
        results.append(self._tune_params(agent, trace, run_id))
        results.append(self._generate_skill(agent, trace, run_id))
        results.append(self._select_skills(agent, trace, run_id))

        # 任一维度成功 → version += 1
        if any(r.success for r in results):
            new_version = old_version + 1
            self._agent_library.update_version(agent_name, new_version)

    def _optimize_prompt(self, agent: Agent, trace: list, run_id: str) -> EvolutionResult:
        """维度 1:分析 trace + LLM 重写 system_prompt。

        LLM 返回相同 prompt → 不写 history,不更新 Agent。
        LLM 返回新 prompt → 写 history + 更新 Agent。
        LLM 失败 → 写 success=False history,不更新 Agent。

        LLM 选择:优先 agent.model,否则 fallback 到 engine.default_model。
        """
        from langchain_core.messages import SystemMessage, HumanMessage
        from agentteam.runtime.evolution_prompts import PROMPT_OPTIMIZER_INSTRUCTION

        # old_prompt 在 try 之前赋值,确保 except 分支也能访问到上下文(I1)
        old_prompt = agent.system_prompt
        try:
            trace_summary = _summarize_trace(trace)

            # C1 修复:生产 ModelProvider.get_llm(None) 会抛 AttributeError,
            # 必须传 ModelRef。优先 agent.model,fallback 到 default_model。
            llm = self._mp.get_llm(agent.model or self._default_model)
            response = llm.invoke([
                SystemMessage(content=PROMPT_OPTIMIZER_INSTRUCTION),
                HumanMessage(content=(
                    f"当前 system_prompt:\n{old_prompt}\n\n"
                    f"本次 run trace:\n{trace_summary}\n\n"
                    f"请基于 trace 分析 prompt 是否需要优化。"
                    f"若需优化给出新版本,若已合理则原样返回。"
                )),
            ])
            new_prompt = _parse_prompt(response.content)

            if new_prompt == old_prompt:
                return EvolutionResult(True, "prompt", "no change needed")

            # Graph Engineering P6: 提案应用前校验 frozen_rules
            ok, reason = self._config.validate_proposal(
                agent, "prompt", old_prompt, new_prompt,
            )
            if not ok:
                # frozen_rule 拒绝:记 history(success=False),不应用变更
                reject_reason = (
                    f"frozen_rule rejected: {reason}"
                    + self._config.anti_metric_warning("prompt")
                )
                self._evolution_repo.add_record(
                    agent_name=agent.name, version=agent.version,
                    dimension="prompt",
                    before_value=old_prompt, after_value=new_prompt,
                    diff=_compute_diff(old_prompt, new_prompt),
                    reason=reject_reason, run_id=run_id, success=False,
                    error=reject_reason,
                )
                return EvolutionResult(False, "prompt", reject_reason)

            self._evolution_repo.add_record(
                agent_name=agent.name, version=agent.version,
                dimension="prompt",
                before_value=old_prompt, after_value=new_prompt,
                diff=_compute_diff(old_prompt, new_prompt),
                reason=response.content + self._config.anti_metric_warning("prompt"),
                run_id=run_id, success=True,
            )
            self._agent_library.update_prompt(agent.name, new_prompt)
            return EvolutionResult(True, "prompt", "prompt updated")
        except Exception as e:
            logger.exception("prompt optimization failed for agent %s", agent.name)
            self._evolution_repo.add_record(
                agent_name=agent.name, version=agent.version,
                dimension="prompt",
                before_value=old_prompt, after_value="",
                diff="", reason=f"error: {e}", run_id=run_id,
                success=False, error=str(e),
            )
            return EvolutionResult(False, "prompt", "error", str(e))

    def _tune_params(self, agent: Agent, trace: list, run_id: str) -> EvolutionResult:
        """维度 2:统计历史 N 次 run + LLM 建议参数调整。

        边界保护:max_iterations 限制 [1, 20]。
        LLM 返回与当前相同 → 不写 history。
        LLM 失败 → 写 success=False history(保留 old_params 上下文)。
        LLM 返回非数字 max_iterations → 跳过本次(no-change),不写 error history。

        简化范围:Task 7 仅处理 max_iterations。approval_policy 涉及
        frozen dataclass 类型转换,LLM 若返回该字段会被 strip(避免
        dict 替换 dataclass 导致类型混乱),留给后续 Task 处理。

        LLM 选择:优先 agent.model,否则 fallback 到 engine.default_model
        (C1 修复:生产 ModelProvider.get_llm(None) 会抛 AttributeError)。
        """
        from langchain_core.messages import SystemMessage, HumanMessage
        from agentteam.runtime.evolution_prompts import PARAM_TUNER_INSTRUCTION
        from dataclasses import asdict
        import json as _json

        # old_params 在 try 之前计算,确保 except 也能访问到上下文(I1)
        old_params = {
            "max_iterations": agent.max_iterations,
            "approval_policy": (
                _json.dumps(asdict(agent.approval_policy), default=str)
                if agent.approval_policy else None
            ),
        }
        try:
            history = self._evolution_repo.list_recent_runs(agent.name, limit=5)
            stats = _compute_stats(history)

            # C1 修复:必须传 ModelRef,优先 agent.model,fallback default_model
            llm = self._mp.get_llm(agent.model or self._default_model)
            response = llm.invoke([
                SystemMessage(content=PARAM_TUNER_INSTRUCTION),
                HumanMessage(content=(
                    f"当前参数: {old_params}\n"
                    f"最近 {len(history)} 次统计: {stats}\n"
                    f"建议调整(只给必要改动,否则返回空 dict)。"
                )),
            ])
            new_params = _parse_params(response.content)

            # Issue 1 修复:Task 7 仅处理 max_iterations,strip approval_policy
            # 避免 LLM 返回的 dict 替换 frozen dataclass 导致类型混乱
            new_params.pop("approval_policy", None)

            # Issue 3 修复:int 转换防御非数字输入
            # LLM 返回 "abc" / null / true 等非数字 → 跳过(no-change),不写 error history
            if "max_iterations" in new_params:
                raw = new_params["max_iterations"]
                try:
                    new_params["max_iterations"] = max(1, min(20, int(float(raw))))
                except (TypeError, ValueError):
                    return EvolutionResult(
                        True, "params",
                        f"skip: max_iterations not numeric: {raw!r}",
                    )

            # 判断是否有变化(只比对 max_iterations)
            has_change = (
                "max_iterations" in new_params
                and new_params["max_iterations"] != agent.max_iterations
            )

            if not has_change:
                return EvolutionResult(True, "params", "no change needed")

            # Graph Engineering P6: 提案应用前校验 frozen_rules
            # (默认 _frozen_max_iterations_range 会再校一次 [1,20],作为最后防线)
            ok, reason = self._config.validate_proposal(
                agent, "params", old_params, new_params,
            )
            if not ok:
                reject_reason = (
                    f"frozen_rule rejected: {reason}"
                    + self._config.anti_metric_warning("params")
                )
                self._evolution_repo.add_record(
                    agent_name=agent.name, version=agent.version,
                    dimension="params",
                    before_value=_json.dumps(old_params, default=str),
                    after_value=_json.dumps(new_params, default=str),
                    diff="", reason=reject_reason, run_id=run_id,
                    success=False, error=reject_reason,
                )
                return EvolutionResult(False, "params", reject_reason)

            self._evolution_repo.add_record(
                agent_name=agent.name, version=agent.version,
                dimension="params",
                before_value=_json.dumps(old_params, default=str),
                after_value=_json.dumps(new_params, default=str),
                diff="",
                reason=response.content + self._config.anti_metric_warning("params"),
                run_id=run_id, success=True,
            )
            self._agent_library.update_params(agent.name, new_params)
            return EvolutionResult(True, "params", "params tuned")
        except Exception as e:
            logger.exception("param tuning failed for agent %s", agent.name)
            self._evolution_repo.add_record(
                agent_name=agent.name, version=agent.version,
                dimension="params",
                before_value=_json.dumps(old_params, default=str),
                after_value="",
                diff="", reason=f"error: {e}", run_id=run_id,
                success=False, error=str(e),
            )
            return EvolutionResult(False, "params", "error", str(e))

    def _generate_skill(self, agent: Agent, trace: list, run_id: str) -> EvolutionResult:
        """维度 3:从成功 run 提炼 skill。

        仅在 run 成功时尝试;LLM 返回 SKIP 跳过;
        生成的 skill 命名 auto_*.md,已存在则附加 _v2/_v3。

        LLM 选择:优先 agent.model,否则 fallback 到 engine.default_model
        (C1 修复:生产 ModelProvider.get_llm(None) 会抛 AttributeError)。
        """
        from langchain_core.messages import SystemMessage, HumanMessage
        from agentteam.runtime.evolution_prompts import SKILL_GENERATOR_INSTRUCTION

        try:
            if not _is_successful_run(trace):
                return EvolutionResult(True, "skill_gen", "run failed, skip")

            if self._skills_dir is None:
                return EvolutionResult(True, "skill_gen", "no skills_dir configured")

            task = _extract_task(trace)
            tool_calls = _extract_tool_calls(trace)
            # Issue 3 防御:final_answer 可能为 None(payload.answer=null),切片会抛 TypeError
            final_answer = _extract_final_answer(trace) or ""

            # C1 修复:必须传 ModelRef,优先 agent.model,fallback default_model
            llm = self._mp.get_llm(agent.model or self._default_model)
            response = llm.invoke([
                SystemMessage(content=SKILL_GENERATOR_INSTRUCTION),
                HumanMessage(content=(
                    f"Agent: {agent.name} (role={agent.role})\n"
                    f"Task: {task}\n"
                    f"Tool calls: {tool_calls}\n"
                    f"Final answer: {final_answer[:500]}\n\n"
                    f"从本次成功执行中提炼可复用的 skill 模式。"
                    f"若无可复用模式则返回 SKIP。"
                    f"否则返回 markdown skill 内容,开头用 '# Skill: auto_<name>' 标注。"
                )),
            ])

            # Issue 1 修复:SKIP 大小写不敏感,避免 LLM 返回 "Skip"/"skip" 触发垃圾文件
            if response.content.strip().upper() == "SKIP":
                return EvolutionResult(True, "skill_gen", "no reusable pattern")

            skill_name, skill_md = _parse_skill_response(response.content)

            # Issue 2 防御:LLM 未按格式返回(无 # Skill: header)→ _parse_skill_response
            # fallback 到 "auto_unknown"。此时不应写文件,避免长期累积 auto_unknown*.md 垃圾。
            if skill_name == "auto_unknown":
                return EvolutionResult(
                    True, "skill_gen",
                    "skip: LLM response missing '# Skill: <name>' header",
                )

            # 处理重名:auto_X.md 已存在 → auto_X_v2.md
            skill_path = self._skills_dir / f"{skill_name}.md"
            if skill_path.exists():
                version = 2
                while (self._skills_dir / f"{skill_name}_v{version}.md").exists():
                    version += 1
                skill_path = self._skills_dir / f"{skill_name}_v{version}.md"

            skill_path.write_text(skill_md, encoding="utf-8")

            # 通知 SkillLoader 重载缓存
            if self._skill_loader is not None:
                self._skill_loader.reload()

            self._evolution_repo.add_record(
                agent_name=agent.name, version=agent.version,
                dimension="skill_gen", before_value="",
                after_value=str(skill_path),
                diff="", reason=f"Generated skill: {skill_name}",
                run_id=run_id, success=True,
            )
            return EvolutionResult(True, "skill_gen", f"generated {skill_name}")
        except Exception as e:
            logger.exception("skill generation failed for agent %s", agent.name)
            self._evolution_repo.add_record(
                agent_name=agent.name, version=agent.version,
                dimension="skill_gen", before_value="", after_value="",
                diff="", reason=f"error: {e}", run_id=run_id,
                success=False, error=str(e),
            )
            return EvolutionResult(False, "skill_gen", "error", str(e))

    def _select_skills(self, agent: Agent, trace: list, run_id: str) -> EvolutionResult:
        """维度 4:任务匹配 skill 软推荐。

        不直接改 Agent.skills,仅写 history 供用户 review 后手动 apply。
        LLM 返回空推荐 → 不写 history。
        LLM 失败 → 写 success=False history(before_value=old_skills, after_value="")。

        LLM 选择:优先 agent.model,否则 fallback 到 engine.default_model
        (C1 修复:生产 ModelProvider.get_llm(None) 会抛 AttributeError)。
        """
        from langchain_core.messages import SystemMessage, HumanMessage
        from agentteam.runtime.evolution_prompts import SKILL_SELECTOR_INSTRUCTION
        import json as _json

        # old_skills 在 try 之前赋值,确保 except 分支能访问到上下文(I1)
        old_skills = _json.dumps(agent.skills) if agent.skills else "[]"
        try:
            if self._skill_loader is None:
                return EvolutionResult(True, "skill_select", "no skill_loader configured")

            available = self._skill_loader.list_available()
            candidates = [s for s in available if s not in agent.skills]
            if not candidates:
                return EvolutionResult(True, "skill_select", "no new skills to recommend")

            # I1 防御:payload.task 显式为 None 时 _extract_task 返回 None,
            # task[:100] 切片会抛 TypeError 被外层 except 捕获,误导排障。
            # 与 _generate_skill 的 final_answer 防御同模式(Issue 3)。
            task = _extract_task(trace) or ""
            # C1 修复:必须传 ModelRef,优先 agent.model,fallback default_model
            llm = self._mp.get_llm(agent.model or self._default_model)
            response = llm.invoke([
                SystemMessage(content=SKILL_SELECTOR_INSTRUCTION),
                HumanMessage(content=(
                    f"Agent: {agent.name}, role={agent.role}\n"
                    f"Task: {task}\n"
                    f"Already equipped skills: {agent.skills}\n"
                    f"Candidate skills: {candidates}\n\n"
                    f"推荐本次任务应装备的 skill(可多选,空则不推荐)。"
                )),
            ])
            recommended = _parse_skill_list(response.content)
            if not recommended:
                return EvolutionResult(True, "skill_select", "no recommendation")

            self._evolution_repo.add_record(
                agent_name=agent.name, version=agent.version,
                dimension="skill_select",
                before_value=old_skills,
                after_value=_json.dumps(recommended),
                diff="", reason=f"Recommended for tasks like: {task[:100]}",
                run_id=run_id, success=True,
            )
            return EvolutionResult(True, "skill_select", f"recommended {recommended}")
        except Exception as e:
            logger.exception("skill selection failed for agent %s", agent.name)
            self._evolution_repo.add_record(
                agent_name=agent.name, version=agent.version,
                dimension="skill_select", before_value=old_skills, after_value="",
                diff="", reason=f"error: {e}", run_id=run_id,
                success=False, error=str(e),
            )
            return EvolutionResult(False, "skill_select", "error", str(e))


def _summarize_trace(trace: list) -> str:
    """把 trace 压缩为 LLM 可读的文本摘要(关键事件 + actor + payload 摘要)。"""
    if not trace:
        return "(empty trace)"
    lines = []
    for ev in trace:
        if not isinstance(ev, dict):
            continue
        ev_type = ev.get("event_type", "unknown")
        actor = ev.get("actor", "")
        payload = ev.get("payload", {})
        # payload 摘要:取前 100 字符
        payload_str = json.dumps(payload, ensure_ascii=False)[:100] if payload else ""
        lines.append(f"[{ev_type}] {actor}: {payload_str}")
    return "\n".join(lines)


def _is_successful_run(trace: list) -> bool:
    """run 成功 = 有 run_end 事件且无 error 事件。"""
    has_run_end = any(
        isinstance(ev, dict) and ev.get("event_type") == "run_end"
        for ev in trace
    )
    has_error = any(
        isinstance(ev, dict) and ev.get("event_type") == "error"
        for ev in trace
    )
    return has_run_end and not has_error


def _extract_task(trace: list) -> str:
    """从 run_start 事件的 payload.task 提取任务描述。"""
    for ev in trace:
        if isinstance(ev, dict) and ev.get("event_type") == "run_start":
            payload = ev.get("payload", {})
            return payload.get("task", "") if isinstance(payload, dict) else ""
    return ""


def _extract_tool_calls(trace: list) -> list[str]:
    """从所有 tool_call 事件提取 tool 名列表(按出现顺序)。"""
    tools = []
    for ev in trace:
        if isinstance(ev, dict) and ev.get("event_type") == "tool_call":
            payload = ev.get("payload", {})
            if isinstance(payload, dict):
                tool = payload.get("tool", "")
                if tool:
                    tools.append(tool)
    return tools


def _extract_final_answer(trace: list) -> str:
    """从 worker_end 事件的 payload.answer 提取最终答案。"""
    for ev in reversed(trace):
        if isinstance(ev, dict) and ev.get("event_type") == "worker_end":
            payload = ev.get("payload", {})
            return payload.get("answer", "") if isinstance(payload, dict) else ""
    return ""


def _compute_diff(old: str, new: str) -> str:
    """计算两段文本的 unified diff。"""
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile="before",
        tofile="after",
        n=3,
    )
    return "".join(diff)


def _compute_stats(history: list) -> dict:
    """从历史 evolution 记录计算统计指标(ParamTuner 用)。

    history 元素为 EvolutionRepo 返回的 dict。
    返回字段:
    - record_count: 记录数
    - success_rate: 成功率
    - has_params_dimension: 是否有 params 维度的记录
    """
    if not history:
        return {}
    total = len(history)
    success_count = sum(1 for h in history if h.get("success"))
    has_params = any(h.get("dimension") == "params" for h in history)
    return {
        "record_count": total,
        "success_rate": success_count / total if total > 0 else 0,
        "has_params_dimension": has_params,
    }


def _parse_prompt(response: str) -> str:
    """从 LLM 响应提取 prompt 文本。

    优先从 ``` 代码块提取;无代码块则返回 trim 后的原文。
    """
    match = re.search(r"```\s*\n?(.*?)\n?```", response, re.DOTALL)
    if match:
        return match.group(1).strip()
    return response.strip()


def _parse_params(response: str) -> dict:
    """从 LLM 响应提取参数 dict。

    优先从 ```json 代码块提取;失败返回空 dict。
    """
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", response, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return {}
    # 尝试直接 parse 整个响应
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        return {}


def _parse_skill_response(response: str) -> tuple[str, str]:
    """从 LLM 响应提取 skill 名 + 内容。

    格式:`# Skill: <name>` 在 markdown 代码块开头或内部。
    返回 (skill_name, skill_md_content)。
    skill_name 仅允许 ASCII 字母/数字/下划线/连字符:
    - 避免捕获 .md 后缀(防止后续构造文件路径产生 auto_x.md.md)
    - 避免捕获 LLM 噪声中文(如 "# Skill: 标注" 应 fallback 到 auto_unknown)
    """
    # 提取 markdown 代码块
    match = re.search(r"```(?:markdown)?\s*\n?(.*?)\n?```", response, re.DOTALL)
    content = match.group(1) if match else response
    # 提取 skill 名(ASCII only,排除 . 与 Unicode 噪声)
    name_match = re.search(r"#\s*Skill:\s*([A-Za-z0-9_-]+)", content)
    skill_name = name_match.group(1) if name_match else "auto_unknown"
    return skill_name, content.strip()


def _parse_skill_list(response: str) -> list[str]:
    """从 LLM 响应提取 skill 名列表。

    支持格式:
    - JSON 数组: ["code_review", "testing"]
    - 逗号分隔(2+ 项): code_review, testing
    - 单个 skill 名(整个 stripped 响应为一个 ASCII 标识符): code_review

    无推荐(如 "no recommendation" / "无推荐")→ 返回空 list。
    """
    if not response or not response.strip():
        return []
    # 尝试 JSON 数组
    match = re.search(r"\[([^\]]+)\]", response)
    if match:
        try:
            arr = json.loads(f"[{match.group(1)}]")
            if isinstance(arr, list):
                return [str(s).strip() for s in arr if str(s).strip()]
        except json.JSONDecodeError:
            pass
    # 逗号分隔(要求 2+ 项,避免误匹配句子中的单词)
    comma_match = re.search(r"[\w_]+(?:\s*,\s*[\w_]+)+", response)
    if comma_match:
        return [s.strip() for s in comma_match.group(0).split(",") if s.strip()]
    # 单个 skill:整个 stripped 响应必须是一个合法 ASCII 标识符(无空格、无其他文本)
    stripped = response.strip()
    if re.fullmatch(r"[A-Za-z_][\w-]*", stripped):
        return [stripped]
    return []
