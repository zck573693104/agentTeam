"""Transcript 双层消息模型 — 借鉴 pi-mono AgentMessage 设计。

核心思想:transcript 消息 ≠ LLM 消息。会话历史可包含 artifact、
notification、compaction-summary 等自定义消息类型,参与持久化和 UI 渲染,
但不污染 LLM 上下文。调用 LLM 前经 convert_to_llm 过滤+转换。

两阶段管道(对标 pi-mono transformContext + convertToLlm):
    TranscriptMessage[] → transform_context() → TranscriptMessage[]
                         → convert_to_llm()   → BaseMessage[] → LLM

- transform_context:在 transcript 层操作,用于上下文窗口管理(剪枝旧消息)、
  注入外部上下文。必须不抛错。
- convert_to_llm:把 transcript 过滤+转换为 LLM 可懂的 BaseMessage[]。
  UI-only 消息(artifact/notification)在此被过滤掉。必须不抛错。
"""
from __future__ import annotations

from typing import Annotated, Union

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, Field


class ArtifactMessage(BaseModel):
    """工件消息:worker 产出的结构化结果。

    参与持久化(进 LangGraph checkpoint)和 UI 渲染,
    但不进 LLM 上下文(convert_to_llm 会过滤掉)。
    对标 pi-mono 的 ArtifactMessage。
    """

    type: str = Field(default="artifact", description="消息类型标识")
    worker: str = Field(description="产出此 artifact 的 worker 名")
    artifact: str = Field(description="实际产物(代码/报告/数据)")
    evidence: list[str] = Field(default_factory=list, description="支撑证据")
    state_delta: dict = Field(default_factory=dict, description="状态变更")
    failure: str | None = Field(default=None, description="失败原因(None=成功)")

    @property
    def id(self) -> str:
        """稳定 id,供 LangGraph add_messages reducer 去重。"""
        return f"artifact:{self.worker}"


class NotificationMessage(BaseModel):
    """通知消息:UI 展示用,不进 LLM 上下文。

    用于记录系统事件(如审批请求、PEP 拒绝、压缩触发),
    供 SSE/UI 回放,但模型看不到。
    """

    type: str = Field(default="notification", description="消息类型标识")
    level: str = Field(default="info", description="info/warning/error")
    message: str = Field(description="通知内容")
    source: str = Field(default="system", description="来源标识")

    @property
    def id(self) -> str:
        import uuid

        return f"notification:{uuid.uuid4().hex}"


class CompactionSummary(BaseModel):
    """压缩摘要:长会话压缩后替代旧消息进 LLM 上下文。

    对标 pi-mono CompactionEntry 的 summary 字段。
    结构化模板:Goal/Constraints/Progress/Decisions/Next Steps/Critical Context。
    """

    type: str = Field(default="compaction_summary", description="消息类型标识")
    summary: str = Field(description="结构化摘要全文")
    first_kept_msg_id: str | None = Field(
        default=None, description="保留尾部消息的起点 id"
    )

    @property
    def id(self) -> str:
        return "compaction_summary"


# Transcript 消息联合类型(对标 pi-mono AgentMessage)
# 新增自定义消息类型时扩展此 Union 即可,无需修改 convert_to_llm 逻辑
TranscriptMessage = Union[BaseMessage, ArtifactMessage, NotificationMessage, CompactionSummary]


def convert_to_llm(messages: list[TranscriptMessage]) -> list[BaseMessage]:
    """过滤+转换 transcript 消息为 LLM 可懂的 BaseMessage 列表。

    规则:
    - BaseMessage 直接保留(SystemMessage/HumanMessage/AIMessage/ToolMessage)
    - CompactionSummary 转为 SystemMessage(让模型看到压缩摘要)
    - ArtifactMessage / NotificationMessage 过滤掉(不进 LLM 上下文)

    必须不抛错(对标 pi-mono convertToLlm 契约)。
    """
    result: list[BaseMessage] = []
    for msg in messages:
        if isinstance(msg, BaseMessage):
            result.append(msg)
        elif isinstance(msg, CompactionSummary):
            result.append(SystemMessage(content=f"[会话摘要]\n{msg.summary}"))
        # ArtifactMessage / NotificationMessage 被过滤
    return result


def transform_context(
    messages: list[TranscriptMessage],
    max_messages: int | None = None,
) -> list[TranscriptMessage]:
    """上下文窗口管理:剪枝旧消息,保留近期消息。

    对标 pi-mono transformContext。当前实现简单截断,后续可扩展为
    基于 token 占用率的智能剪枝 + compaction summary 注入。

    必须不抛错(对标 pi-mono transformContext 契约)。
    """
    if max_messages is None or len(messages) <= max_messages:
        return list(messages)
    return list(messages[-max_messages:])
