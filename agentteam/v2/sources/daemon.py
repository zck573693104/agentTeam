"""SourceDaemon — 常驻轮询守护(dsh: 24h 事件驱动;asyncio,不阻塞 HTTP 面)。

注册 poll 型 Source(GitHub/飞书),周期 poll() → 触发 InboundEngine 处理。
webhook 型源不走 daemon(由 HTTP 路由驱动)。
"""
from __future__ import annotations

import asyncio

from agentteam.logging_config import get_logger
from agentteam.v2.sources.protocol import SourceBase, Trigger

logger = get_logger("v2.sources.daemon")


class SourceDaemon:
    def __init__(
        self,
        sources: list[SourceBase],
        handle: "callable[[Trigger], dict]",  # noqa: F722 (InboundEngine.handle)
        poll_interval: float = 10.0,
    ) -> None:
        self._sources = [s for s in sources if s.poll is not SourceBase.poll]
        self._handle = handle
        self._interval = poll_interval
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        """挂靠 FastAPI lifespan 的事件循环(asyncio.create_task)。"""
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._loop(), name="v2-source-daemon")
            logger.info("SourceDaemon started with %d poll sources", len(self._sources))

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            for src in self._sources:
                try:
                    triggers: list[Trigger] = await asyncio.to_thread(src.poll)
                    for t in triggers:
                        # 失败静默隔离(dsh: 错误不影响其他源与 HTTP 面)
                        try:
                            await asyncio.to_thread(self._handle, src.name, t)
                        except Exception:
                            logger.exception("source %s trigger handle failed", src.name)
                except Exception:
                    logger.exception("source %s poll failed", src.name)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except TimeoutError:
                pass
