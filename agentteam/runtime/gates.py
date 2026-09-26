"""Gate Registry — 审批门注册表。

借鉴 RoleRegistry 的数据驱动模式,把 step_gate/worker_gate 从
TeamCompiler._compile_supervisor 中解耦出来。core 不内置任何 gate,
gate 作为示例/扩展由上层注册。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable


@dataclass
class GateNode:
    """审批门节点定义。由 GateFactory.create 返回。"""

    name: str
    node_fn: Callable
    route_after: Callable[[dict], str] | None = None
    insert_before: str = ""


@runtime_checkable
class GateFactory(Protocol):
    """审批门工厂协议。返回 None 表示该 agent 不需要 gate。"""

    def create(
        self,
        agent: Any,
        child_targets: dict[str, str],
        compiler_deps: dict[str, Any] | None = None,
    ) -> GateNode | None: ...


class GateRegistry:
    """审批门注册表:level → factory。数据驱动,对标 RoleRegistry。"""

    def __init__(self) -> None:
        self._factories: dict[str, GateFactory] = {}

    def register(self, level: str, factory: GateFactory) -> None:
        self._factories[level] = factory

    def get(self, level: str) -> GateFactory | None:
        return self._factories.get(level)

    def levels(self) -> list[str]:
        return list(self._factories.keys())

    def clear(self) -> None:
        self._factories.clear()


_gates: GateRegistry = GateRegistry()


def get_gates() -> GateRegistry:
    """获取全局 GateRegistry 单例。"""
    return _gates


def reset_gates() -> None:
    """重置全局 GateRegistry(测试用)。"""
    _gates.clear()
