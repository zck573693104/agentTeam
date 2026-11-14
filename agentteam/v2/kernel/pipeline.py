"""工具执行 pipeline(洋葱模型 stage;审批门在 P1 作为外层 stage 接入)。

v1 的 ToolCallPipeline/HookRegistry 是零注册的扩展点;v2 直接把
trace 与(P1 的)approval 作为生产 stage 注册,保证扩展点真的被使用。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from langchain_core.tools import BaseTool

from agentteam.v2.domain import events as ev
from agentteam.v2.storage.events import EventBus, EventRepo


@dataclass
class ToolContext:
    """一次工具调用的上下文(session 级;tool_call_id 由 session 每次调用前更新)。"""

    ticket_id: str
    session_id: str
    worker_name: str
    workspace_dir: str
    approval_targets: list[str] = field(default_factory=list)  # 审批门读取
    tool_call_id: str | None = None


StageFn = Callable[[BaseTool, dict, "ToolContext", Callable[[dict], str]], str]


def _innermost(tool: BaseTool, args: dict, _ctx: ToolContext) -> str:
    result = tool.invoke(args)
    return result if isinstance(result, str) else str(result)


class ToolPipeline:
    """stage 洋葱:先注册的在外层(可短路);最内层执行真实工具调用。"""

    def __init__(self) -> None:
        self._stages: list[StageFn] = []

    def add_stage(self, stage: StageFn) -> None:
        self._stages.append(stage)

    def execute(self, tool: BaseTool, args: dict, ctx: ToolContext) -> str:
        call: Callable[[BaseTool, dict, ToolContext], str] = _innermost
        for stage in reversed(self._stages):
            call = self._wrap(stage, call)
        return call(tool, args, ctx)

    @staticmethod
    def _wrap(stage: StageFn, inner: Callable[[BaseTool, dict, ToolContext], str]) -> StageFn:
        def wrapped(tool: BaseTool, args: dict, ctx: ToolContext) -> str:
            return stage(tool, args, ctx, lambda a: inner(tool, a, ctx))
        return wrapped


def make_trace_stage(event_repo: EventRepo, bus: EventBus) -> StageFn:
    """轨迹 stage:tool_called/tool_result 事件落库 + 扇出。"""

    def stage(tool: BaseTool, args: dict, ctx: ToolContext, next_call: Callable[[dict], str]) -> str:
        payload = {
            "tool": tool.name,
            "args": _preview(args),
            "worker": ctx.worker_name,
        }
        envelope = event_repo.insert(ctx.ticket_id, ev.TOOL_CALLED, actor=ctx.worker_name,
                                     session_id=ctx.session_id, payload=payload)
        bus.publish(ctx.ticket_id, envelope)
        try:
            result = next_call(args)
        except Exception as exc:  # 工具异常不终结会话,worker 可自纠
            result = f"工具执行错误: {exc}"
        ok = not result.startswith("工具执行错误")
        done_payload = {
            "tool": tool.name,
            "ok": ok,
            "result_preview": result[:400],
        }
        envelope = event_repo.insert(ctx.ticket_id, ev.TOOL_RESULT, actor=ctx.worker_name,
                                     session_id=ctx.session_id, payload=done_payload)
        bus.publish(ctx.ticket_id, envelope)
        return result

    return stage


def _preview(args: dict, limit: int = 300) -> Any:
    """参数预览(写文件 content 截断,防事件流膨胀)。"""
    out = {}
    for k, v in args.items():
        s = v if isinstance(v, (int, float, bool)) else str(v)
        if isinstance(s, str) and len(s) > limit:
            s = s[:limit] + "…"
        out[k] = s
    # 大 JSON 序列化安全网
    try:
        json.dumps(out, ensure_ascii=False)
    except (TypeError, ValueError):
        return {"raw": str(args)[:limit]}
    return out
