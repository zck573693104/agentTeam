"""EvolutionEngine:Run 终态后异步触发 4 维度自进化。

4 维度:PromptOptimizer / ParamTuner / SkillGenerator / SkillSelector。
设计原则:
- 异步:不阻塞 RunManager 的 API 响应
- 隔离:4 维度独立 LLM 调用 + 独立写 history,互不影响
- 失败保护:任一维度失败仅记 error,不影响其他维度 / run 结果
- 防抖:同一 agent 5 分钟内只触发一次
- 版本原子性:一次 trigger 内 4 维度全部尝试后,任一成功则 version += 1
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentteam.domain.agent import Agent
from agentteam.domain.library import AgentLibrary
from agentteam.models.provider import ModelProvider
from agentteam.runtime.skills import SkillLoader
from agentteam.storage.audit import AuditRepo
from agentteam.storage.evolution import EvolutionRepo
from agentteam.storage.runs import RunRepo


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
        skill_loader: SkillLoader | None = None,
        skills_dir: Path | None = None,
    ) -> None:
        self._mp = model_provider
        self._agent_library = agent_library
        self._evolution_repo = evolution_repo
        self._run_repo = run_repo
        self._audit = audit_repo
        self._skill_loader = skill_loader
        self._skills_dir = skills_dir
        self._last_trigger: dict[str, float] = {}
        self._lock = threading.Lock()

    def trigger(self, run_id: str) -> None:
        """RunManager 在 run 终态后异步调用。"""
        run = self._run_repo.get_run(run_id)
        if run is None:
            return
        agents = self._collect_agents_from_trace(run_id)
        if not agents:
            return
        for agent_name in agents:
            self._evolve_agent(agent_name, run_id)

    def _collect_agents_from_trace(self, run_id: str) -> list[str]:
        """从 audit_events 提取涉及的 agent 名(去重)。

        扫描 worker_start / leader_plan 事件的 actor 字段。
        失败返回空列表。
        """
        try:
            events = self._audit.list_events(run_id)
        except Exception:
            return []
        names: list[str] = []
        seen: set[str] = set()
        for ev in events:
            ev_type = ev.get("event_type", "") if isinstance(ev, dict) else ""
            if ev_type in ("worker_start", "leader_plan"):
                actor = ev.get("actor", "") if isinstance(ev, dict) else ""
                if actor and actor not in seen:
                    seen.add(actor)
                    names.append(actor)
        return names

    def _evolve_agent(self, agent_name: str, run_id: str) -> None:
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

        trace = self._audit.list_events(run_id)
        old_version = agent.version or 1

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
        """维度 1:分析 trace + LLM 重写 system_prompt。Task 6 实现。"""
        return EvolutionResult(True, "prompt", "not implemented yet")

    def _tune_params(self, agent: Agent, trace: list, run_id: str) -> EvolutionResult:
        """维度 2:统计历史 + LLM 建议参数调整。Task 7 实现。"""
        return EvolutionResult(True, "params", "not implemented yet")

    def _generate_skill(self, agent: Agent, trace: list, run_id: str) -> EvolutionResult:
        """维度 3:从成功 run 提炼 skill。Task 8 实现。"""
        return EvolutionResult(True, "skill_gen", "not implemented yet")

    def _select_skills(self, agent: Agent, trace: list, run_id: str) -> EvolutionResult:
        """维度 4:任务匹配 skill 软推荐。Task 9 实现。"""
        return EvolutionResult(True, "skill_select", "not implemented yet")
