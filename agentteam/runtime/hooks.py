"""Hook Registry — 统一的事件钩子机制。

借鉴 pi-mono ExtensionAPI 的 on(event, handler) 模式,
替代当前散落在各处的硬编码触发点(如 RunManager._trigger_evolution_async)。

设计要点:
- 钩子按事件类型分组,同事件的 handler 按注册顺序串行执行
- handler 异常不中断主流程(记日志后继续下一个 handler)
- 全局单例 + 可注入实例(测试用)
- 事件类型用 Literal 约束,避免拼写错误

支持的事件(对标 pi-mono ExtensionAPI 的 ~25 个钩子,按需扩展):
- pre_compile / post_compile:编译前后(可改写 Team/compiled graph)
- pre_run / post_run:run 生命周期
- run_settled:run 终态后(替代固定 EvolutionEngine.trigger)
- pre_tool_call / post_tool_call:工具调用前后(可拦截/改写结果)
- pre_leader_plan / post_leader_review:计划与验收前后
- worker_start / worker_end:worker 执行前后
- approval_requested / approval_decided:审批生命周期

钩子签名:
- handler(ctx: dict) -> None | dict
  返回 None:不修改 ctx
  返回 dict:合并到 ctx(用于 pre_* 钩子改写上下文)
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger("agentteam.hooks")

# 事件类型约束(对标 pi-mono ExtensionAPI 事件枚举)
HookEvent = str  # 用 str 而非 Literal,允许扩展自定义事件


class HookRegistry:
    """钩子注册表:事件 → 回调列表。

    线程安全:用 threading.Lock 保护 _hooks。
    回调按注册顺序串行执行(对标 pi-mono listener 串行 await 语义)。
    """

    def __init__(self) -> None:
        self._hooks: dict[str, list[Callable]] = defaultdict(list)
        self._lock = threading.Lock()

    def on(self, event: HookEvent, handler: Callable) -> None:
        """注册钩子。

        Args:
            event: 事件类型(如 "pre_tool_call")。
            handler: 回调函数,签名 handler(ctx: dict) -> None | dict。
        """
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

        Args:
            event: 事件类型。
            ctx: 上下文 dict,会传递给每个 handler。

        Returns:
            更新后的 ctx(handler 返回的 dict 会合并进 ctx)。

        handler 异常不中断主流程,记日志后继续下一个 handler。
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


# 全局单例
_hooks: HookRegistry = HookRegistry()


def get_hooks() -> HookRegistry:
    """获取全局 HookRegistry 单例。"""
    return _hooks


def reset_hooks() -> None:
    """重置全局 HookRegistry(测试用,清空所有注册)。"""
    _hooks.clear()
