"""Gate Registry — 审批门注册表。

借鉴 RoleRegistry 的数据驱动模式,把 step_gate/worker_gate 从
TeamCompiler._compile_supervisor 中解耦出来。

设计要点:
- GateFactory:根据 agent 配置生成 gate 节点 + 边
- GateRegistry:level → factory 映射
- _compile_supervisor 从 registry 取 factory,不硬编码 make_step_gate/make_worker_gate

当前 agentteam 已删除成品层(approval.py),core 不内置任何 gate。
gate 作为示例/扩展由上层注册(如 presets 或独立 governance 模块)。

集成方式(在 _compile_supervisor 中):
    gate_factory = gate_registry.get(agent.approval_policy.level)
    if gate_factory:
        gate_result = gate_factory.create(agent, child_targets, ...)
        # gate_result.node_fn 加入 graph
        # gate_result.edges 加入边
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable


@dataclass
class GateNode:
    """审批门节点定义。

    由 GateFactory.create 返回,包含节点函数和边定义。
    _compile_supervisor 据此把 gate 加入 StateGraph。
    """

    name: str  # gate 节点名(如 "step_gate" / "worker_{name}_gate")
    node_fn: Callable  # 节点函数(可被 graph.add_node 调用)
    # gate 之后的路由函数:返回目标节点名或 END
    # 拒绝→END,批准→下一节点
    route_after: Callable[[dict], str] | None = None
    # gate 在图中的位置描述(供编译器加边)
    insert_before: str = ""  # gate 后接的节点名


@runtime_checkable
class GateFactory(Protocol):
    """审批门工厂协议。

    根据 agent 配置生成 gate 节点。返回 None 表示该 agent 不需要 gate。
    """

    def create(
        self,
        agent: Any,  # Agent dataclass
        child_targets: dict[str, str],
        compiler_deps: dict[str, Any] | None = None,
    ) -> GateNode | None: ...


class GateRegistry:
    """审批门注册表:level → factory。

    数据驱动,对标 RoleRegistry。
    TeamCompiler._compile_supervisor 从此 registry 取 factory,
    不再硬编码 make_step_gate/make_worker_gate。
    """

    def __init__(self) -> None:
        self._factories: dict[str, GateFactory] = {}

    def register(self, level: str, factory: GateFactory) -> None:
        """注册 gate factory。

        Args:
            level: 审批级别(如 "step" / "worker" / "tool")。
            factory: GateFactory 实现。
        """
        self._factories[level] = factory

    def get(self, level: str) -> GateFactory | None:
        """按 level 取 factory,未注册返回 None。"""
        return self._factories.get(level)

    def levels(self) -> list[str]:
        """返回已注册的全部 level。"""
        return list(self._factories.keys())

    def clear(self) -> None:
        """清空注册(测试用)。"""
        self._factories.clear()


# 全局单例
_gates: GateRegistry = GateRegistry()


def get_gates() -> GateRegistry:
    """获取全局 GateRegistry 单例。"""
    return _gates


def reset_gates() -> None:
    """重置全局 GateRegistry(测试用)。"""
    _gates.clear()
