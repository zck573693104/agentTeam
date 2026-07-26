"""Transcript 双层消息模型 — 借鉴 pi-mono AgentMessage 设计。

核心思想:transcript 消息 ≠ LLM 消息。会话历史可包含 artifact、
notification、compaction-summary 等自定义消息类型,参与持久化和 UI 渲染,
但不污染 LLM 上下文。调用 LLM 前经 convert_to_llm 过滤+转换。
"""
from __future__ import annotations

from typing import Union

from langchain_core.messages import BaseMessage, SystemMessage
from pydantic import BaseModel, Field


class ArtifactMessage(BaseModel):
    """工件消息:worker 产出的结构化结果。参与持久化和 UI,不进 LLM 上下文。"""

    type: str = Field(default="artifact")
    worker: str = Field(description="产出此 artifact 的 worker 名")
    artifact: str = Field(description="实际产物")
    evidence: list[str] = Field(default_factory=list)
    state_delta: dict = Field(default_factory=dict)
    failure: str | None = Field(default=None)

    @property
    def id(self) -> str:
        return f"artifact:{self.worker}"


class NotificationMessage(BaseModel):
    """通知消息:UI 展示用,不进 LLM 上下文。"""

    type: str = Field(default="notification")
    level: str = Field(default="info")
    message: str = Field()
    source: str = Field(default="system")

    @property
    def id(self) -> str:
        import uuid
        return f"notification:{uuid.uuid4().hex}"


class CompactionSummary(BaseModel):
    """压缩摘要:长会话压缩后替代旧消息进 LLM 上下文。"""

    type: str = Field(default="compaction_summary")
    summary: str = Field(description="结构化摘要全文")
    first_kept_msg_id: str | None = Field(default=None)

    @property
    def id(self) -> str:
        return "compaction_summary"


TranscriptMessage = Union[BaseMessage, ArtifactMessage, NotificationMessage, CompactionSummary]


def convert_to_llm(messages: list[TranscriptMessage]) -> list[BaseMessage]:
    """过滤+转换 transcript 消息为 LLM 可懂的 BaseMessage 列表。

    BaseMessage 直接保留;CompactionSummary 转为 SystemMessage;
    ArtifactMessage/NotificationMessage 过滤掉。必须不抛错。
    """
    result: list[BaseMessage] = []
    for msg in messages:
        if isinstance(msg, BaseMessage):
            result.append(msg)
        elif isinstance(msg, CompactionSummary):
            result.append(SystemMessage(content=f"[会话摘要]\n{msg.summary}"))
    return result


def transform_context(
    messages: list[TranscriptMessage],
    max_messages: int | None = None,
) -> list[TranscriptMessage]:
    """上下文窗口管理:剪枝旧消息。必须不抛错。"""
    if max_messages is None or len(messages) <= max_messages:
        return list(messages)
    return list(messages[-max_messages:])
