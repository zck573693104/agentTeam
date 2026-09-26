"""Hook Registry — 统一的事件钩子机制。

借鉴 pi-mono ExtensionAPI 的 on(event, handler) 模式,
替代当前散落在各处的硬编码触发点(如 RunManager._trigger_evolution_async)。

handler 异常不中断主流程(记日志后继续下一个 handler)。
回调按注册顺序串行执行(对标 pi-mono listener 串行 await 语义)。
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Callable

logger = logging.getLogger("agentteam.hooks")

HookEvent = str  # 用 str 而非 Literal,允许扩展自定义事件


class HookRegistry:
    """钩子注册表:事件 → 回调列表。线程安全。"""

    def __init__(self) -> None:
        self._hooks: dict[str, list[Callable]] = defaultdict(list)
        self._lock = threading.Lock()

    def on(self, event: HookEvent, handler: Callable) -> None:
        """注册钩子。handler 签名:handler(ctx: dict) -> None | dict。"""
        with self._lock:
            self._hooks[event].append(handler)

    def off(self, event: HookEvent, handler: Callable) -> None:
        """注销钩子。"""
        with self._lock:
            if event in self._hooks:
                self._hooks[event] = [
                    h for h in self._hooks[event] if h is not handler
                ]

    def emit(self, event: HookEvent, ctx: dict | None = None) -> dict:
        """同步触发事件,回调按注册顺序串行执行。

        handler 返回 dict 会合并进 ctx。handler 异常不中断主流程。
        """
        if ctx is None:
            ctx = {}
        with self._lock:
            handlers = list(self._hooks.get(event, []))
        for handler in handlers:
            try:
                result = handler(ctx)
                if isinstance(result, dict):
                    ctx.update(result)
            except Exception:
                logger.exception(
                    "Hook %s for event '%s' failed", handler, event
                )
        return ctx

    def clear(self) -> None:
        """清空所有钩子(测试用)。"""
        with self._lock:
            self._hooks.clear()

    def handlers(self, event: HookEvent) -> list[Callable]:
        """返回某事件的全部 handler(只读视图,测试用)。"""
        with self._lock:
            return list(self._hooks.get(event, []))


_hooks: HookRegistry = HookRegistry()


def get_hooks() -> HookRegistry:
    """获取全局 HookRegistry 单例。"""
    return _hooks


def reset_hooks() -> None:
    """重置全局 HookRegistry(测试用)。"""
    _hooks.clear()
