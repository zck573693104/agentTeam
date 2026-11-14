"""Source 协议基类(dsh: 入站源 → Trigger{actor, thread_key, text, at})。

安全铁律(spec §8):
- 外部文本是数据不是规则:Source 只产出 Trigger 文本,不得改变授权;
- 身份分离:每个 Source 是独立 agent 身份,禁止与操作者个人账号混用。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field


class Trigger(BaseModel):
    """入站触发事件(不可变:frozen,外部入站数据禁止就地改写)。"""

    model_config = ConfigDict(frozen=True)

    actor: str = Field(description="触发者(Source 自己的身份体系,不跨源映射)")
    thread_key: str = Field(description="线程键(同会话/同 issue 归一线程)")
    text: str = Field(description="入站文本(纯数据)")
    at: str | None = Field(default=None, description="发生时间(ISO;缺省由服务端落库时间)")
    raw: dict[str, Any] = Field(default_factory=dict, description="原始载荷(审计用)")


class SourceBase(ABC):
    """入站源基类。

    - push 型(webhook): 外部系统回调 HTTP,由 API 路由接事件后调 handle;
    - poll 型(轮询): SourceDaemon 常驻循环调 poll() 拉取新事件。
    """

    name: str

    def __init__(self, name: str, config: dict, emit: Callable[[Trigger], dict]) -> None:
        self.name = name
        self.config = config
        self._emit = emit  # InboundEngine.handle 的回调

    def poll(self) -> list[Trigger]:
        """轮询型源拉取新事件;push 型返回空(由 webhook 路由驱动)。"""
        return []
