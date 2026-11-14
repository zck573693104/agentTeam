"""Worker Session — 每 Ticket 一个会话的执行器(v2 内核的心脏,替代 langgraph 图)。

设计:
- 纯 Python ReAct 循环:system(+skills+纪律) + task → llm.bind_tools(tools).invoke
  → 有 tool_calls 则执行并回灌 ToolMessage → 直到无 tool_calls(终答)或迭代耗尽
- checkpoint = messages 表 transcript(每轮落库);恢复时从 transcript 重载续跑
- 无 tool_calls 的终答解析 QUESTION 标记(P1 升级为 Attention;P0 记入轨迹)
- 事件全落 events 表并扇出 SSE

拓扑红线(dsh):worker 只汇报进工单轨迹,不直接对人。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from agentteam.logging_config import get_logger
from agentteam.models.provider import ModelProvider, ModelRef
from agentteam.tools.registry import ToolRegistry
from agentteam.v2.domain import events as ev
from agentteam.v2.domain.discipline import WORKER_DISCIPLINE, extract_question
from agentteam.v2.domain.events import SessionStatus
from agentteam.v2.domain.worker import WorkerDef
from agentteam.v2.kernel.pipeline import ToolPipeline, ToolContext, make_trace_stage
from agentteam.v2.kernel.workspace import FILE_TOOL_NAMES, make_file_tools
from agentteam.v2.storage.events import EventBus, EventRepo
from agentteam.v2.storage.tickets import MessageRepo, SessionRepo

logger = get_logger("v2.kernel.session")


@dataclass
class SessionResult:
    status: SessionStatus
    reply: str | None = None
    question: str | None = None
    error: str | None = None


def _message_from_row(row: dict[str, Any]):
    """transcript 行 → langchain 消息(恢复会话时重载)。"""
    role = row["role"]
    if role == "system":
        return SystemMessage(content=row["content"])
    if role == "user":
        return HumanMessage(content=row["content"])
    if role == "assistant":
        return AIMessage(content=row["content"], tool_calls=row["tool_calls"] or [])
    if role == "tool":
        return ToolMessage(content=row["content"], tool_call_id=row["tool_call_id"] or "")
    raise ValueError(f"未知消息角色: {role}")


def _persist_message(repo: MessageRepo, session_id: str, msg) -> None:
    """langchain 消息 → transcript 落库。"""
    tool_calls = None
    tool_call_id = None
    if isinstance(msg, AIMessage):
        tool_calls = [
            {"name": tc["name"], "args": tc["args"], "id": tc["id"]}
            for tc in (msg.tool_calls or [])
        ] or None
    elif isinstance(msg, ToolMessage):
        tool_call_id = msg.tool_call_id
    if isinstance(msg, SystemMessage):
        role = "system"
    elif isinstance(msg, HumanMessage):
        role = "user"
    elif isinstance(msg, AIMessage):
        role = "assistant"
    else:
        role = "tool"
    content = msg.content if isinstance(msg.content, str) else str(msg.content)
    repo.append(session_id, role, content, tool_calls=tool_calls, tool_call_id=tool_call_id)


class WorkerSession:
    """一个 Ticket 的一个执行尝试。run() 返回终态,由 Scheduler 落 ticket 状态。"""

    def __init__(
        self,
        *,
        ticket,                     # Ticket
        session_id: str,
        worker: WorkerDef,
        workspace_dir: Path,
        model_provider: ModelProvider,
        tool_registry: ToolRegistry,
        skill_loader,               # SkillLoader
        ticket_repo: TicketRepo,
        session_repo: SessionRepo,
        message_repo: MessageRepo,
        event_repo: EventRepo,
        bus: EventBus,
        pipeline: ToolPipeline | None = None,
        default_model: ModelRef | None = None,
        resume: bool = False,
    ) -> None:
        self.ticket = ticket
        self.session_id = session_id
        self.worker = worker
        self.workspace_dir = workspace_dir
        self.ticket_repo = ticket_repo
        self.session_repo = session_repo
        self.message_repo = message_repo
        self.event_repo = event_repo
        self.bus = bus

        # —— 工具:workspace 文件工具(每 ticket 隔离) + 注册表非文件工具,一次构建 ——
        self._tools: list = list(make_file_tools(workspace_dir))
        extra_names = [n for n in worker.tools if n not in FILE_TOOL_NAMES]
        if extra_names:
            self._tools.extend(tool_registry.get_tools(extra_names))

        # —— pipeline:默认自带 trace stage;P1 外层再挂审批门 ——
        self.pipeline = pipeline or ToolPipeline()
        if not self.pipeline._stages:
            self.pipeline.add_stage(make_trace_stage(event_repo, bus))

        # —— 模型 ——
        ref = ModelRef(**worker.model) if worker.model else \
            (default_model or ModelRef("qwen", "qwen-max"))
        self.llm = model_provider.get_llm(ref).bind_tools(self._tools)

        # —— 组装 system 消息(skills 注入格式沿用 v1) ——
        skills = skill_loader.load(worker.skills)
        skill_block = "\n\n".join(
            f'<skill name="{name}">\n{content}\n</skill>' for name, content in skills.items()
        )
        system_parts = [p for p in (worker.system_prompt, skill_block, WORKER_DISCIPLINE) if p]
        self._system_message = SystemMessage(content="\n\n".join(system_parts))
        self._resume = resume

    # —— 事件辅助 ——
    def _emit(self, event_type: str, payload: dict, actor: str | None = None) -> None:
        envelope = self.event_repo.insert(
            self.ticket.id, event_type, actor=actor or self.worker.name,
            session_id=self.session_id, payload=payload,
        )
        self.bus.publish(self.ticket.id, envelope)

    def _build_task_message(self) -> HumanMessage:
        scope = self.ticket.mandate.scope if self.ticket.mandate else None
        parts = [self.ticket.task]
        if scope:
            parts.append(f"【授权范围】{scope}(越界操作视为失败)")
        return HumanMessage(content="\n".join(parts))

    def run(self) -> SessionResult:
        """执行到终态。异常不向外抛(转 failed + 事件),由 Scheduler 消费返回值。"""
        try:
            return self._run_inner()
        except Exception as exc:  # 防御:模型/网络异常转 failed,不崩调度线程
            logger.exception("session %s crashed", self.session_id)
            self.session_repo.cas_status(self.session_id, SessionStatus.RUNNING, SessionStatus.FAILED)
            self._emit(ev.SESSION_ENDED, {"status": "failed", "error": str(exc)[:500]})
            return SessionResult(status=SessionStatus.FAILED, error=str(exc))

    def _run_inner(self) -> SessionResult:
        # —— transcript:恢复时重载,否则初始化 system+task ——
        rows = self.message_repo.list_for_session(self.session_id)
        if self._resume and rows:
            messages = [_message_from_row(r) for r in rows]
            self._emit(ev.SESSION_STARTED, {"worker": self.worker.name, "resumed": True})
        else:
            messages = [self._system_message, self._build_task_message()]
            for m in messages:
                _persist_message(self.message_repo, self.session_id, m)
            self._emit(ev.SESSION_STARTED, {"worker": self.worker.name, "resumed": False})

        max_iterations = self.worker.max_iterations
        result = SessionResult(status=SessionStatus.FAILED, error="迭代耗尽")

        for iteration in range(1, max_iterations + 1):
            self.session_repo.set_iterations(self.session_id, iteration)
            ai: AIMessage = self.llm.invoke(messages)
            _persist_message(self.message_repo, self.session_id, ai)
            messages.append(ai)

            tool_calls = ai.tool_calls or []
            if tool_calls:
                for tc in tool_calls:
                    tool_result = self._execute_tool(tc)
                    tool_msg = ToolMessage(content=tool_result, tool_call_id=tc["id"])
                    _persist_message(self.message_repo, self.session_id, tool_msg)
                    messages.append(tool_msg)
                self._emit(ev.SESSION_ITERATION, {
                    "iteration": iteration, "tools": [tc["name"] for tc in tool_calls],
                })
                continue

            # —— 终答:解析 QUESTION(P1 转 Attention;P0 记入事件) ——
            reply = ai.content if isinstance(ai.content, str) else str(ai.content)
            result = SessionResult(
                status=SessionStatus.DONE, reply=reply, question=extract_question(reply),
            )
            self._emit(ev.SESSION_ITERATION, {"iteration": iteration, "final": True})
            break

        self.session_repo.cas_status(self.session_id, SessionStatus.RUNNING, result.status)
        self._emit(ev.SESSION_ENDED, {
            "status": result.status.value,
            "reply": (result.reply or "")[:2000] if result.reply else None,
            "question": result.question,
            "error": result.error,
        })
        return result

    def _execute_tool(self, tc: dict) -> str:
        """单工具调用(经 pipeline;未知工具返回错误文本让 worker 自纠)。"""
        ctx = ToolContext(
            ticket_id=self.ticket.id,
            session_id=self.session_id,
            worker_name=self.worker.name,
            workspace_dir=str(self.workspace_dir),
            approval_targets=list(self.worker.approval_targets),
        )
        tool = next((t for t in self._tools if t.name == tc["name"]), None)
        if tool is None:
            return f"错误: 未知工具 '{tc['name']}'"
        return self.pipeline.execute(tool, dict(tc["args"] or {}), ctx)
