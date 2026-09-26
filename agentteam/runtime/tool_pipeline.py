"""ToolCall Pipeline — 可插拔的工具调用流水线。

借鉴 pi-mono 的 beforeToolCall/afterToolCall 钩子,把 make_tool_step
内嵌的 PEP 拦截 + 审批 + 执行三件事解耦为可插拔 stage。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ToolCallContext:
    """工具调用上下文,贯穿整个 pipeline。"""

    run_id: str
    agent_name: str
    tool_call: dict[str, Any]
    state: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCallResult:
    """工具调用结果。"""

    tool_call_id: str
    content: str
    is_error: bool = False
    terminate: bool = False


@runtime_checkable
class ToolCallStage(Protocol):
    """工具调用流水线阶段协议。

    process 返回 ToolCallResult 表示短路(后续 stage 不执行);
    返回 None 表示放行,继续下一 stage。
    """

    def process(self, ctx: ToolCallContext) -> ToolCallResult | None: ...


class ExecutionStage:
    """执行阶段:实际调用工具。默认作为 pipeline 最后一个 stage。"""

    def __init__(self, tool_map: dict[str, Any]) -> None:
        self._tool_map = tool_map

    def process(self, ctx: ToolCallContext) -> ToolCallResult | None:
        name = ctx.tool_call.get("name", "")
        tool = self._tool_map.get(name)
        if tool is None:
            return ToolCallResult(
                tool_call_id=ctx.tool_call.get("id", ""),
                content=f"工具 '{name}' 未注册",
                is_error=True,
            )
        args = ctx.tool_call.get("args", {})
        try:
            if hasattr(tool, "invoke"):
                result = tool.invoke(args)
            else:
                result = tool(**args)
            content = str(result) if not isinstance(result, str) else result
            return ToolCallResult(
                tool_call_id=ctx.tool_call.get("id", ""),
                content=content,
            )
        except Exception as e:
            return ToolCallResult(
                tool_call_id=ctx.tool_call.get("id", ""),
                content=f"工具执行失败: {type(e).__name__}: {e}",
                is_error=True,
            )


class ToolCallPipeline:
    """工具调用流水线:按顺序执行各 stage。"""

    def __init__(self, stages: list[ToolCallStage] | None = None) -> None:
        self._stages: list[ToolCallStage] = list(stages or [])

    def add_stage(self, stage: ToolCallStage) -> None:
        self._stages.append(stage)

    def add_stages(self, stages: list[ToolCallStage]) -> None:
        self._stages.extend(stages)

    def stages(self) -> list[ToolCallStage]:
        return list(self._stages)

    def execute(self, ctx: ToolCallContext) -> ToolCallResult:
        """执行流水线,返回首个短路 stage 的结果。"""
        for stage in self._stages:
            try:
                result = stage.process(ctx)
            except Exception as e:
                return ToolCallResult(
                    tool_call_id=ctx.tool_call.get("id", ""),
                    content=f"流水线阶段 {type(stage).__name__} 异常: {type(e).__name__}: {e}",
                    is_error=True,
                )
            if result is not None:
                return result
        return ToolCallResult(
            tool_call_id=ctx.tool_call.get("id", ""),
            content="流水线无执行阶段",
            is_error=True,
        )


def build_default_pipeline(tool_map: dict[str, Any]) -> ToolCallPipeline:
    """构建默认 pipeline:仅 ExecutionStage。core 不内置审批/PEP 等成品 stage。"""
    pipeline = ToolCallPipeline()
    pipeline.add_stage(ExecutionStage(tool_map))
    return pipeline
