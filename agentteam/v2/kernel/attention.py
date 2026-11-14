"""Attention 内核组件:审批门 stage + 暂停信号(dsh: 审批 = 授权延续/终止)。"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from langchain_core.tools import BaseTool

from agentteam.v2.domain import events as ev
from agentteam.v2.kernel.pipeline import ToolContext
from agentteam.v2.kernel.workspace import unified_diff_for_write
from agentteam.v2.storage.attentions import AttentionRepo
from agentteam.v2.storage.events import EventBus, EventRepo


class AttentionNeeded(Exception):
    """审批门短路信号:session 捕获后转 waiting_human。"""

    def __init__(self, attention_id: str, tool: str) -> None:
        self.attention_id = attention_id
        self.tool = tool
        super().__init__(f"attention {attention_id} for tool {tool}")


def _needs_approval(approval_targets: list[str], tool_name: str) -> bool:
    if not approval_targets:
        return False
    return "*" in approval_targets or tool_name in approval_targets


def make_approval_gate_stage(
    attention_repo: AttentionRepo, event_repo: EventRepo, bus: EventBus,
) -> Callable:
    """审批门 stage(洋葱外层):命中审批目标的工具 → 建 Attention → 暂停会话。

    dsh 铁律:一次最多一个 Attention。session 已在 waiting 状态时不会再
    执行工具(线程已返回),故此处天然满足;防御性检查兜底。
    """

    def stage(tool: BaseTool, args: dict, ctx: ToolContext, next_call) -> str:
        if _needs_approval(ctx.approval_targets, tool.name):
            if attention_repo.has_pending_for_session(ctx.session_id):
                # 防御:不该发生;出现则按拒绝处理并提示
                return "错误: 已有未决的人工确认,请先等待其完成"
            payload = {
                "tool": tool.name,
                "args": {k: (str(v)[:300]) for k, v in args.items()},
                "worker": ctx.worker_name,
                "tool_call_id": ctx.tool_call_id,
                "decision_effect": "approve=授权延续并执行;reject=该操作终止(worker 可改道)",
            }
            # B7 diff review: 写文件审批展示 unified diff(旧→新)
            if tool.name == "write_file":
                try:
                    payload["diff"] = unified_diff_for_write(
                        Path(ctx.workspace_dir), args.get("path", ""),
                        str(args.get("content", "")),
                    )[:8000]
                except Exception:
                    pass
            att = attention_repo.create(
                ctx.ticket_id, ctx.session_id, "approval", payload,
            )
            envelope = event_repo.insert(
                ctx.ticket_id, ev.ATTENTION_CREATED, actor=ctx.worker_name,
                session_id=ctx.session_id,
                payload={"attention_id": att["id"], "type": "approval", "tool": tool.name},
            )
            bus.publish(ctx.ticket_id, envelope)
            raise AttentionNeeded(att["id"], tool.name)
        return next_call(args)

    return stage
