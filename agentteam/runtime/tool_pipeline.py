"""ToolCall Pipeline — 可插拔的工具调用流水线。

借鉴 pi-mono 的 beforeToolCall/afterToolCall 钩子 + AgentToolResult.terminate 设计,
把 make_tool_step 内嵌的 PEP 拦截 + 审批 + 执行三件事解耦为可插拔 stage。

设计要点:
- ToolCallStage:无状态阶段,返回 ToolCallResult 表示短路(后续 stage 不执行),
  返回 None 表示继续下一阶段
- ToolCallPipeline:按顺序执行 stages,首个短路的 result 作为最终结果
- 默认 stage 顺序:HookStage(pre) → ExecutionStage
  (审批/PEP 等成品功能通过 hooks 注入,不在 core 里)
- Stage 可读取 ctx.state 做决策,也可修改 ctx(如注入额外信息)

对标 pi-mono:
- beforeToolCall 可 {block: true} 阻止 → 对应 stage 返回 error ToolCallResult
- afterToolCall 可覆盖 content/details/isError/terminate → 对应 post-stage
- AgentToolResult.terminate → ToolCallResult.terminate
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ToolCallContext:
    """工具调用上下文,贯穿整个 pipeline。

    每个 tool_call 独立一个 context实例,stage 可读写其中的字段。
    """

    run_id: str
    agent_name: str
    tool_call: dict[str, Any]  # {name, args, id, type}
    state: dict[str, Any]  # 当前 LangGraph state 快照(只读视图)
    # stage 间传递的额外信息(如审批决策、PEP 评估结果)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCallResult:
    """工具调用结果。

    无论哪个 stage 短路,结果都封装为 ToolCallResult。
    最终由 make_tool_step 转为 ToolMessage 写回 state。
    """

    tool_call_id: str
    content: str
    is_error: bool = False
    # terminate=True 表示工具请求提前终止 agent loop
    # (对标 pi-mono AgentToolResult.terminate)
    terminate: bool = False


@runtime_checkable
class ToolCallStage(Protocol):
    """工具调用流水线阶段协议。

    实现要点:
    - process 返回 ToolCallResult 表示短路(后续 stage 不执行)
    - process 返回 None 表示放行,继续下一 stage
    - 不应抛异常(异常由 pipeline 兜底转为 error result)
    """

    def process(self, ctx: ToolCallContext) -> ToolCallResult | None: ...


class ExecutionStage:
    """执行阶段:实际调用工具。

    默认作为 pipeline 的最后一个 stage。
    前面的 stage 都未短路时,由此 stage 执行工具。
    """

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
            # 兼容 BaseTool.invoke 和 callable
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
    """工具调用流水线:按顺序执行各 stage。

    借鉴 pi-mono 的 beforeToolCall → execute → afterToolCall 三段式,
    但更通用:支持任意数量的 stage。

    使用方式:
        pipeline = ToolCallPipeline()
        pipeline.add_stage(MyValidationStage())
        pipeline.add_stage(ExecutionStage(tool_map))
        result = pipeline.execute(ctx)
    """

    def __init__(self, stages: list[ToolCallStage] | None = None) -> None:
        self._stages: list[ToolCallStage] = list(stages or [])

    def add_stage(self, stage: ToolCallStage) -> None:
        """添加 stage 到流水线末尾。"""
        self._stages.append(stage)

    def add_stages(self, stages: list[ToolCallStage]) -> None:
        """批量添加 stage。"""
        self._stages.extend(stages)

    def stages(self) -> list[ToolCallStage]:
        """返回当前所有 stage(只读视图)。"""
        return list(self._stages)

    def execute(self, ctx: ToolCallContext) -> ToolCallResult:
        """执行流水线,返回首个短路 stage 的结果。

        若所有 stage 都未短路(返回 None),返回默认 error result。
        stage 抛异常时转为 error result,不中断 pipeline。
        """
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
        # 所有 stage 都未短路(理论上 ExecutionStage 总会短路)
        return ToolCallResult(
            tool_call_id=ctx.tool_call.get("id", ""),
            content="流水线无执行阶段",
            is_error=True,
        )


def build_default_pipeline(tool_map: dict[str, Any]) -> ToolCallPipeline:
    """构建默认 pipeline:仅 ExecutionStage。

    core 不内置审批/PEP 等成品 stage,由上层通过 hooks 注入。
    """
    pipeline = ToolCallPipeline()
    pipeline.add_stage(ExecutionStage(tool_map))
    return pipeline
