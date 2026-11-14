"""Worker Session — 每 Ticket 一个会话的执行器(v2 内核的心脏)。

设计:
- 纯 Python ReAct 循环:system(+skills+纪律) + task → llm.bind_tools(tools).invoke
  → 有 tool_calls 则经 pipeline 执行并回灌 ToolMessage → 直到终答/迭代耗尽
- checkpoint = messages 表 transcript(每轮落库);恢复时从 transcript 重载续跑
- Attention(P1): 审批门短路(AttentionNeeded)或终答 QUESTION → 会话转 waiting_human;
  人工决定后 resume() 从 transcript 续跑
- 撤权: is_cancelled 回调每轮检查;取消 = 中断 + 状态落库(dsh: 人的停止 = 撤权)

拓扑红线(dsh):worker 只汇报进工单轨迹,不直接对人。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

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
from agentteam.v2.kernel.attention import AttentionNeeded, make_approval_gate_stage
from agentteam.v2.kernel.pipeline import ToolPipeline, ToolContext, make_trace_stage
from agentteam.v2.kernel.workspace import FILE_TOOL_NAMES, make_file_tools

# A3 chain-of-recall: 触发压缩的估算 token 阈值(chars/4)与保留消息数
_COMPACT_THRESHOLD_CHARS = 60_000
_COMPACT_KEEP_LAST = 8
from agentteam.v2.storage.attentions import AttentionRepo
from agentteam.v2.storage.events import EventBus, EventRepo
from agentteam.v2.storage.tickets import MessageRepo, SessionRepo

logger = get_logger("v2.kernel.session")


@dataclass
class SessionResult:
    status: SessionStatus
    reply: str | None = None
    question: str | None = None
    error: str | None = None
    attention_id: str | None = None


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
    """一个 Ticket 的一个执行尝试。"""

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
        ticket_repo: TicketRepo,    # noqa: F821 (类型注解用,运行时仅存引用)
        session_repo: SessionRepo,
        message_repo: MessageRepo,
        event_repo: EventRepo,
        attention_repo: AttentionRepo,
        bus: EventBus,
        pipeline: ToolPipeline | None = None,
        default_model: ModelRef | None = None,
        is_cancelled: Callable[[str], bool] | None = None,
        pop_follow_ups: Callable[[], list[tuple[str, str]]] | None = None,
    ) -> None:
        self.ticket = ticket
        self.session_id = session_id
        self.worker = worker
        self.workspace_dir = workspace_dir
        self.ticket_repo = ticket_repo
        self.session_repo = session_repo
        self.message_repo = message_repo
        self.event_repo = event_repo
        self.attention_repo = attention_repo
        self.bus = bus
        self._is_cancelled = is_cancelled or (lambda _tid: False)
        self._pop_follow_ups = pop_follow_ups or (lambda: [])

        # —— 工具:workspace 文件工具(每 ticket 隔离) + 注册表非文件工具 ——
        # MCP 挂载:worker 声明的 mcp_servers 注册进注册表(幂等,带缓存),再按名取用
        from agentteam.domain.mcp_server import MCPServer
        for m in worker.mcp_servers:
            try:
                tool_registry.register_mcp_tools(MCPServer(**m))
            except Exception as exc:  # MCP 启动失败不阻塞会话,工具缺失时 worker 自纠
                logger.warning("mcp register failed for %s/%s: %s", worker.name, m.get("name"), exc)
        self._tools: list = list(make_file_tools(workspace_dir))
        extra_names = [n for n in worker.tools if n not in FILE_TOOL_NAMES]
        if extra_names:
            self._tools.extend(tool_registry.get_tools(extra_names))

        # —— pipeline:trace + 审批门(外层),dsh: 审批 = 授权管理 ——
        self.pipeline = pipeline or ToolPipeline()
        if not self.pipeline._stages:
            self.pipeline.add_stage(make_approval_gate_stage(attention_repo, event_repo, bus))
            self.pipeline.add_stage(make_trace_stage(event_repo, bus))

        # —— 模型 ——
        ref = ModelRef(**worker.model) if worker.model else \
            (default_model or ModelRef("qwen", "qwen-max"))
        self.llm = model_provider.get_llm(ref).bind_tools(self._tools)
        # A3 摘要用无工具绑定的裸模型(避免摘要轮误触发工具调用)
        self._plain_llm = model_provider.get_llm(ref)

        skills = skill_loader.load(worker.skills)
        skill_block = "\n\n".join(
            f'<skill name="{name}">\n{content}\n</skill>' for name, content in skills.items()
        )
        system_parts = [p for p in (worker.system_prompt, skill_block, WORKER_DISCIPLINE) if p]
        self._system_message = SystemMessage(content="\n\n".join(system_parts))

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

    def _tool_context(self, tool_call_id: str | None = None) -> ToolContext:
        return ToolContext(
            ticket_id=self.ticket.id,
            session_id=self.session_id,
            worker_name=self.worker.name,
            workspace_dir=str(self.workspace_dir),
            approval_targets=list(self.worker.approval_targets),
            tool_call_id=tool_call_id,
        )

    # —— 入口 ——
    def _reload_messages(self, rows: list[dict]) -> list:
        """transcript 行 → langchain 消息;A3 压缩摘要行排在原 system 之后。"""
        msgs = [_message_from_row(r) for r in rows]
        summaries = [m for m in msgs
                     if isinstance(m, SystemMessage) and str(m.content).startswith("【历史压缩")]
        if not summaries:
            return msgs
        rest = [m for m in msgs if m not in summaries]
        head, tail = rest[:1], rest[1:]
        return head + summaries + tail

    def run(self) -> SessionResult:
        """首次/续跑执行到终态或暂停。异常不外抛(转 failed + 事件)。"""
        try:
            rows = self.message_repo.list_for_session(self.session_id)
            resumed = bool(rows)
            if resumed:
                messages = self._reload_messages(rows)
            else:
                messages = [self._system_message, self._build_task_message()]
                for m in messages:
                    _persist_message(self.message_repo, self.session_id, m)
            self._emit(ev.SESSION_STARTED, {"worker": self.worker.name, "resumed": resumed})
            return self._loop(messages)
        except AttentionNeeded as a:
            # 审批门短路:transcript 已落库,人工决定后 resume 续跑
            self.session_repo.cas_status(self.session_id, SessionStatus.RUNNING, SessionStatus.WAITING_HUMAN)
            return SessionResult(status=SessionStatus.WAITING_HUMAN, attention_id=a.attention_id)
        except Exception as exc:
            logger.exception("session %s crashed", self.session_id)
            self.session_repo.cas_status(self.session_id, SessionStatus.RUNNING, SessionStatus.FAILED)
            self._emit(ev.SESSION_ENDED, {"status": "failed", "error": str(exc)[:500]})
            return SessionResult(status=SessionStatus.FAILED, error=str(exc))

    def resume_after_decision(self, attention: dict, decision: str, answer: str | None) -> SessionResult:
        """人工决定后恢复:按决定注入消息/执行工具,然后继续主循环。"""
        # 会话转回 running(与注入消息同事务语义:先 cas 再改 transcript)
        self.session_repo.cas_status(self.session_id, SessionStatus.WAITING_HUMAN, SessionStatus.RUNNING)
        if attention["type"] == "budget" and decision == "reject":
            self.session_repo.cas_status(self.session_id, SessionStatus.RUNNING, SessionStatus.FAILED)
            self._emit(ev.SESSION_ENDED, {"status": "failed", "error": "预算耗尽,人工选择终止"})
            return SessionResult(status=SessionStatus.FAILED, error="预算耗尽,人工选择终止")
        if attention["type"] == "budget":
            pass  # approve: 预算已由 scheduler 加额落库;继续主循环
        elif attention["type"] == "approval" and decision == "approve":
            # 授权延续:真正执行该工具(绕过审批门,但保留轨迹)
            result = self._execute_approved_tool(attention)
        elif attention["type"] == "approval":
            result = f"人工拒绝: 该操作授权终止({(attention['payload'] or {}).get('tool')})"
        else:  # question
            result = f"【人工回答】{answer or '(空)'}"
        self.message_repo.append(self.session_id, "tool" if attention["type"] == "approval" else "user",
                                 result,
                                 tool_call_id=(attention["payload"] or {}).get("tool_call_id")
                                 if attention["type"] == "approval" else None)
        if attention["type"] == "approval" and decision == "approve":
            self._emit(ev.TOOL_RESULT, {
                "tool": (attention["payload"] or {}).get("tool"),
                "ok": not result.startswith("错误"),
                "result_preview": result[:400],
                "approved": True,
                "approved_by": attention.get("decided_by") or "human",
            })
        return self.run()

    # —— 主循环 ——
    def _loop(self, messages: list) -> SessionResult:
        max_iterations = self.worker.max_iterations
        result = SessionResult(status=SessionStatus.FAILED, error="迭代耗尽")

        for iteration in range(1, max_iterations + 1):
            if self._is_cancelled(self.ticket.id):
                self.session_repo.cas_status(self.session_id, SessionStatus.RUNNING, SessionStatus.CANCELLED)
                return SessionResult(status=SessionStatus.CANCELLED)
            # 追问直通(dsh Subscription fan-out): 迭代边界注入,只入不回
            for text, by in self._pop_follow_ups():
                if not text:
                    continue
                follow = HumanMessage(content=f"【追问·{by}】{text}")
                _persist_message(self.message_repo, self.session_id, follow)
                messages.append(follow)
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

            reply = ai.content if isinstance(ai.content, str) else str(ai.content)
            question = extract_question(reply)
            if question:
                # dsh: Attention = 等待人的结构化问题(一次一个;会话已暂停故天然满足)
                att = self.attention_repo.create(
                    self.ticket.id, self.session_id, "question",
                    {"question": question, "draft_reply": reply[:1000], "worker": self.worker.name},
                )
                envelope = self.event_repo.insert(
                    self.ticket.id, ev.ATTENTION_CREATED, actor=self.worker.name,
                    session_id=self.session_id,
                    payload={"attention_id": att["id"], "type": "question", "question": question},
                )
                self.bus.publish(self.ticket.id, envelope)
                self.session_repo.cas_status(self.session_id, SessionStatus.RUNNING, SessionStatus.WAITING_HUMAN)
                return SessionResult(status=SessionStatus.WAITING_HUMAN,
                                     reply=reply, question=question, attention_id=att["id"])

            result = SessionResult(status=SessionStatus.DONE, reply=reply)
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

    # —— A1 预算 / A3 压缩 ——
    def _account_tokens(self, ai: AIMessage) -> None:
        """从模型响应提取 usage 并累加(各 provider 字段名不一,宽容解析)。"""
        usage = getattr(ai, "usage_metadata", None) or {}
        meta = getattr(ai, "response_metadata", None) or {}
        tin = int(usage.get("input_tokens") or meta.get("prompt_tokens") or 0)
        tout = int(usage.get("output_tokens") or meta.get("completion_tokens") or 0)
        if tin or tout:
            self.session_repo.add_tokens(self.session_id, tin, tout)

    def _budget_exceeded(self) -> bool:
        if not self.ticket.budget_tokens:
            return False
        total = self.ticket_repo.ticket_tokens(self.ticket.id)
        return total > self.ticket.budget_tokens

    def _raise_budget_attention(self) -> SessionResult:
        used = self.ticket_repo.ticket_tokens(self.ticket.id)
        att = self.attention_repo.create(
            self.ticket.id, self.session_id, "budget",
            {"used": used, "limit": self.ticket.budget_tokens,
             "decision_effect": "approve = 预算翻倍继续;reject = 终止工单"},
        )
        envelope = self.event_repo.insert(
            self.ticket.id, ev.ATTENTION_CREATED, actor=self.worker.name,
            session_id=self.session_id,
            payload={"attention_id": att["id"], "type": "budget",
                     "used": used, "limit": self.ticket.budget_tokens},
        )
        self.bus.publish(self.ticket.id, envelope)
        self.session_repo.cas_status(self.session_id, SessionStatus.RUNNING, SessionStatus.WAITING_HUMAN)
        return SessionResult(status=SessionStatus.WAITING_HUMAN, attention_id=att["id"])

    def _maybe_compact(self, messages: list) -> None:
        """A3 chain-of-recall:估算 token 超阈值 → 蒸馏旧轮次(失败静默跳过)。"""
        chars = sum(len(str(m.content)) for m in messages)
        if chars < _COMPACT_THRESHOLD_CHARS:
            return
        summary = self._summarize_history(messages)
        if not summary:
            return
        if self.message_repo.compact(self.session_id, _COMPACT_KEEP_LAST, summary):
            rows = self.message_repo.list_for_session(self.session_id)
            messages[:] = self._reload_messages(rows)
            self._emit(ev.SESSION_ITERATION, {"compacted": True, "summary": summary[:300]})

    def _summarize_history(self, messages: list) -> str | None:
        """轻量模型蒸馏:任务目标/已完成步骤/关键工件/重要决定。失败返回 None。"""
        transcript = chr(10).join(
            "[" + type(m).__name__ + "] " + str(m.content)[:400] for m in messages[-40:]
        )
        prompt = (
            "压缩以下 agent 会话历史为要点(不超过300字),保留:任务目标、已完成步骤、"
            "关键工件路径、重要决定。直接输出要点:" + chr(10) + transcript
        )
        try:
            resp = self._plain_llm.invoke(prompt)
            text = resp.content if isinstance(resp.content, str) else str(resp.content)
            return text[:2000] or None
        except Exception:
            tools_used = []
            for m in messages:
                if isinstance(m, AIMessage) and m.tool_calls:
                    tools_used += [tc["name"] for tc in m.tool_calls]
            if not tools_used:
                return None
            return "会话曾调用工具: " + str(sorted(set(tools_used))) + "(原文已压缩)"

    # —— 工具执行 ——
    def _execute_tool(self, tc: dict) -> str:
        tool = next((t for t in self._tools if t.name == tc["name"]), None)
        if tool is None:
            return f"错误: 未知工具 '{tc['name']}'"
        return self.pipeline.execute(tool, dict(tc["args"] or {}), self._tool_context(tc["id"]))

    def _execute_approved_tool(self, attention: dict) -> str:
        """审批通过后的真实执行(绕过审批门,保留轨迹)。"""
        payload = attention.get("payload") or {}
        tool_name = payload.get("tool")
        tool = next((t for t in self._tools if t.name == tool_name), None)
        if tool is None:
            return f"错误: 未知工具 '{tool_name}'"
        ctx = self._tool_context(payload.get("tool_call_id"))
        envelope = self.event_repo.insert(
            self.ticket.id, ev.TOOL_CALLED, actor=self.worker.name,
            session_id=self.session_id,
            payload={"tool": tool.name, "args": payload.get("args", {}), "worker": self.worker.name,
                     "approved": True},
        )
        self.bus.publish(self.ticket.id, envelope)
        try:
            result = tool.invoke(dict(payload.get("args") or {}))
            if not isinstance(result, str):
                result = str(result)
        except Exception as exc:
            result = f"工具执行错误: {exc}"
        return result
