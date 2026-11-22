"""PostmortemEngine — 工单终态复盘蒸馏(P4 学习闭环的蒸馏层)。

范式(Letta sleep-time compute):主 agent(WorkerSession)无权写记忆;
记忆蒸馏由独立的 sleep-time 角色在工单终态后异步执行 —— 空闲期整理,
"干净、精炼、可检索"的教训替代流水账轨迹。

运行纪律:
- 独立单线程池,不占会话池坑位;失败只落 postmortem_failed 事件,永不影响工单状态
- 幂等:一个工单最多一次复盘尝试(事件表查重,跨重启有效)
- 产出:教训 → MemoryStore;可复用流程 → proposal 注意件(人工审批晋升技能)

P5 housekeeping(sleep-time 角色兼职夜班保洁,每次 run 顺带执行):
- 观察模式确认样本攒够 → 蒸馏入站研判校准规则(eval_distill)
- 从未命中且超期的教训 → 归档(memory.evict_stale)
- 超期终态工单工作区 → 归档(workspace_sweeper 回调)
"""
from __future__ import annotations

from typing import Callable

from pydantic import BaseModel, Field

from agentteam.logging_config import get_logger
from agentteam.models.provider import ModelProvider, ModelRef
from agentteam.v2.domain import events as ev
from agentteam.v2.domain.discipline import POSTMORTEM_PROMPT
from agentteam.v2.domain.events import TicketStatus
from agentteam.v2.kernel.memory import LessonDraft, MemoryStore
from agentteam.v2.storage.attentions import AttentionRepo
from agentteam.v2.storage.events import EventBus, EventRepo
from agentteam.v2.storage.tickets import MessageRepo, SessionRepo, TicketRepo

logger = get_logger("v2.kernel.postmortem")

_MIN_MESSAGES = 4            # system+user+终答 = 3 行的琐碎会话不值得复盘
_MAX_TRANSCRIPT_CHARS = 30_000
_MAX_ROW_CHARS = 2_000
_TRANSCRIPT_ROLES = {"system": "系统", "user": "任务", "assistant": "工人",
                     "tool": "工具"}


class LessonItem(BaseModel):
    """复盘提炼的一条教训。"""

    condition: str = Field(description="触发条件:什么仓库/命令/场景下适用")
    lesson: str = Field(description="可执行做法(具体到命令/参数/步骤)")
    scope: str = Field(default="repo", description="repo(仅该仓库适用) 或 global(跨项目通用)")
    existing_id: str | None = Field(
        default=None, description="判重命中时填账本里的 lesson id,合并更新而非新建",
    )


class SkillProposal(BaseModel):
    """可复用流程的技能提案(需人工审批后才写入技能库)。"""

    name: str = Field(description="技能文件名(小写字母/数字/连字符,不含扩展名)")
    content: str = Field(description="完整 markdown 技能正文")
    summary: str = Field(default="", description="一句话说明该技能解决什么问题")


class PostmortemResult(BaseModel):
    """复盘蒸馏的结构化输出。"""

    outcome_summary: str = Field(default="", description="一句话结果评价")
    lessons: list[LessonItem] = Field(default_factory=list, description="0~3 条教训")
    skill_proposal: SkillProposal | None = Field(
        default=None, description="值得固化为技能的可复用流程(可空)",
    )


class PostmortemEngine:
    """终态复盘蒸馏器(Letta sleep-time 角色)。"""

    def __init__(
        self,
        *,
        memory_store: MemoryStore,
        model_provider: ModelProvider,
        aux_model: ModelRef,
        ticket_repo: TicketRepo,
        session_repo: SessionRepo,
        message_repo: MessageRepo,
        event_repo: EventRepo,
        attention_repo: AttentionRepo,
        bus: EventBus,
        min_messages: int = _MIN_MESSAGES,
        max_transcript_chars: int = _MAX_TRANSCRIPT_CHARS,
        memory_evict_days: int = 0,        # P5: 教训淘汰天数;0 = 关闭
        distiller=None,                    # P5: InboundDistiller | None
        workspace_sweeper: Callable[[], object] | None = None,  # P5: 工作区归档回调
    ) -> None:
        self.memory = memory_store
        self._mp = model_provider
        self._aux_model = aux_model
        self.tickets = ticket_repo
        self.sessions = session_repo
        self.messages = message_repo
        self.events = event_repo
        self.attentions = attention_repo
        self.bus = bus
        self._min_messages = min_messages
        self._max_transcript_chars = max_transcript_chars
        self._memory_evict_days = memory_evict_days
        self._distiller = distiller
        self._workspace_sweeper = workspace_sweeper

    # —— 事件辅助(工单终态后追加,不换状态) ——
    def _emit(self, ticket_id: str, event_type: str, payload: dict,
              session_id: str | None = None) -> None:
        envelope = self.events.insert(ticket_id, event_type, actor="postmortem",
                                      session_id=session_id, payload=payload)
        self.bus.publish(ticket_id, envelope)

    # —— 输入组装 ——
    @staticmethod
    def _transcript_text(rows: list[dict], max_chars: int) -> str:
        lines: list[str] = []
        for r in rows:
            content = (r.get("content") or "").strip()
            if r.get("role") == "assistant" and r.get("tool_calls"):
                calls = ", ".join(
                    f"{tc.get('name')}({json_args})"[:120]
                    for tc in r["tool_calls"]
                    if (json_args := str(tc.get("args", ""))[:80])
                )
                content = f"{content}\n[调用工具] {calls}" if content else f"[调用工具] {calls}"
            line = f"[{_TRANSCRIPT_ROLES.get(r.get('role', ''), r.get('role', ''))}] " \
                   f"{content[:_MAX_ROW_CHARS]}"
            lines.append(line)
        text = "\n\n".join(lines)
        return text[-max_chars:]  # 保尾段(结论与纠错集中在后段)

    def _already_done(self, ticket_id: str) -> bool:
        for e in self.events.list_after(ticket_id):
            if e["type"] in (ev.POSTMORTEM_COMPLETED, ev.POSTMORTEM_FAILED):
                return True
        return False

    # —— 入口(Scheduler 在工单终态后经独立单线程池调用) ——
    def run(self, ticket_id: str) -> None:
        """复盘一个终态工单;任何失败只落事件,绝不抛出。"""
        self._housekeeping()  # sleep-time 兼职夜班保洁(失败不阻断复盘)
        try:
            self._run(ticket_id)
        except Exception as exc:
            logger.exception("postmortem for ticket %s failed", ticket_id)
            try:
                self._emit(ticket_id, ev.POSTMORTEM_FAILED,
                           payload={"error": str(exc)[:300]})
            except Exception:  # 事件通道也坏 → 只能记日志
                logger.exception("postmortem failed-event emit failed for %s", ticket_id)

    def _housekeeping(self) -> None:
        """顺带保洁:蒸馏校准规则 / 淘汰陈旧教训 / 归档超期工作区。逐项隔离失败。"""
        if self._distiller is not None:
            try:
                self._distiller.maybe_distill()
            except Exception:
                logger.exception("eval rules distill housekeeping failed")
        if self._memory_evict_days > 0:
            try:
                self.memory.evict_stale(self._memory_evict_days)
            except Exception:
                logger.exception("memory evict housekeeping failed")
        if self._workspace_sweeper is not None:
            try:
                self._workspace_sweeper()
            except Exception:
                logger.exception("workspace archive housekeeping failed")

    def _run(self, ticket_id: str) -> None:
        ticket = self.tickets.get(ticket_id)
        if ticket is None or ticket.status not in (TicketStatus.DONE, TicketStatus.FAILED):
            return
        if self._already_done(ticket_id):
            return
        ticket_sessions = self.sessions.list_for_ticket(ticket_id)
        if not ticket_sessions:
            return  # 无会话(直答/worker 缺失)无轨迹可复盘
        session_id = ticket_sessions[-1]["id"]
        rows = self.messages.list_for_session(session_id)
        if len(rows) < self._min_messages:
            return  # 琐碎会话(无工具交互)不值得蒸馏
        task_block = (
            f"任务: {ticket.task}\n"
            f"结果: {ticket.status.value}\n"
            f"结果说明: {(ticket.result or '')[:500]}\n"
            f"仓库: {ticket.repo_path or '(无)'}"
        )
        lesson_index = "\n".join(self.memory.l0_lines(ticket.repo_path, limit=30)) or "(空)"
        prompt = POSTMORTEM_PROMPT.format(
            task_block=task_block,
            transcript=self._transcript_text(rows, self._max_transcript_chars),
            lesson_index=lesson_index,
        )
        llm = self._mp.get_llm(self._aux_model)
        result = llm.with_structured_output(PostmortemResult).invoke(prompt)
        result = result if isinstance(result, PostmortemResult) else PostmortemResult(**result)

        # 教训入库:repo scope 仅在确有仓库时生效,否则一律 global
        lesson_ids: list[str] = []
        lesson_count = 0
        repo_drafts = [LessonDraft(condition=i.condition, lesson=i.lesson,
                                   existing_id=i.existing_id)
                       for i in result.lessons if i.scope == "repo" and ticket.repo_path]
        global_drafts = [LessonDraft(condition=i.condition, lesson=i.lesson,
                                     existing_id=i.existing_id)
                         for i in result.lessons if not (i.scope == "repo" and ticket.repo_path)]
        for store_repo, drafts in ((ticket.repo_path, repo_drafts), (None, global_drafts)):
            if not drafts:
                continue
            for w in self.memory.add_lessons(store_repo, drafts):
                lesson_ids.append(w["id"])
                lesson_count += 1

        # 技能提案 → 注意件(人工审批晋升;通知走 ATTENTION_CREATED 现有链路)
        skill_name: str | None = None
        proposal = result.skill_proposal
        if proposal and proposal.name and proposal.content:
            skill_name = proposal.name
            self.attentions.create(
                ticket_id, session_id, "proposal",
                payload={
                    "skill_name": proposal.name,
                    "content": proposal.content[:20_000],
                    "summary": (proposal.summary or "")[:300],
                    "worker": "postmortem",
                    "decision_effect": "approve = 写入技能库并热加载;reject = 丢弃提案",
                },
            )
            self._emit(ticket_id, ev.SKILL_PROPOSED, payload={
                "skill_name": skill_name,
                "summary": (proposal.summary or "")[:200],
            }, session_id=session_id)

        self._emit(ticket_id, ev.POSTMORTEM_COMPLETED, payload={
            "outcome_summary": (result.outcome_summary or "")[:300],
            "lesson_count": lesson_count,
            "lesson_ids": lesson_ids,
            "skill_proposed": skill_name,
        }, session_id=session_id)
