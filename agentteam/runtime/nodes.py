from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from agentteam.domain.team import Leader
from agentteam.domain.worker import Worker
from agentteam.runtime.state import TeamState
from agentteam.runtime.trace import TraceWriter


class PlanStep(BaseModel):
    """计划中的一步：指派给某 worker 的子任务。"""

    worker: str = Field(description="执行此步的 worker name")
    instruction: str = Field(description="子任务描述")


class Plan(BaseModel):
    """Leader 拆解出的执行计划。"""

    steps: list[PlanStep] = Field(description="按顺序执行的步骤列表")


def make_leader_plan_node(
    leader: Leader, llm: BaseChatModel, trace_writer: TraceWriter | None = None
):
    """创建 leader_plan 节点：用 LLM 结构化输出把 task 拆成 plan。"""

    def leader_plan(state: TeamState) -> dict:
        run_id = state.get("run_id", "")
        task = state["task"]
        messages = [
            SystemMessage(content=leader.system_prompt),
            HumanMessage(
                content=f"请把以下任务拆解成可执行的步骤计划，每步指派一个 worker：\n\n{task}"
            ),
        ]
        structured = llm.with_structured_output(Plan)
        plan_obj = structured.invoke(messages)
        plan = [
            {"worker": s.worker, "instruction": s.instruction, "status": "pending"}
            for s in plan_obj.steps
        ]
        if trace_writer:
            trace_writer.emit(run_id, "leader_plan", leader.name, {"steps": len(plan)})
        return {
            "plan": plan,
            "current_step": 0,
            "messages": [
                AIMessage(content=f"[Leader] 计划已拆解：{len(plan)} 步", name=leader.name)
            ],
            "audit_events": [{"event_type": "leader_plan", "actor": leader.name}],
        }

    return leader_plan


def make_init_worker(
    worker: Worker,
    trace_writer: TraceWriter | None = None,
):
    """创建 init_worker 节点：初始化 ReAct 循环的 react_messages 和计数器。"""

    def init_worker(state: TeamState) -> dict:
        run_id = state.get("run_id", "")
        step = state["plan"][state["current_step"]]
        instruction = step["instruction"]

        if trace_writer:
            trace_writer.emit(run_id, "worker_start", worker.name)

        return {
            "react_messages": [
                SystemMessage(content=worker.system_prompt),
                HumanMessage(content=instruction),
            ],
            "tool_calls": [],
            "iteration": 0,
            "final_answer": "",
        }

    return init_worker


def make_agent_step(
    worker: Worker,
    llm: BaseChatModel,
    tools: list[BaseTool],
):
    """创建 agent_step 节点：LLM 决策调用工具或给出最终答案。"""

    llm_with_tools = llm.bind_tools(tools) if tools else llm

    def agent_step(state: dict) -> dict:
        react_messages = state.get("react_messages", [])
        response = llm_with_tools.invoke(react_messages)

        tool_calls = getattr(response, "tool_calls", None)
        if tool_calls:
            return {
                "react_messages": [response],
                "tool_calls": tool_calls,
                "final_answer": "",
            }
        return {
            "react_messages": [response],
            "tool_calls": [],
            "final_answer": response.content,
        }

    return agent_step


def make_finalize(
    worker: Worker,
    trace_writer: TraceWriter | None = None,
):
    """创建 finalize 节点：写 worker_outputs、汇总 messages、emit worker_end。"""

    def finalize(state: dict) -> dict:
        run_id = state.get("run_id", "")
        final_answer = state.get("final_answer", "")

        # max_iterations 达上限时，用最后一条 AIMessage 兜底
        if not final_answer:
            react_messages = state.get("react_messages", [])
            for msg in reversed(react_messages):
                if isinstance(msg, AIMessage):
                    final_answer = msg.content
                    break

        if trace_writer:
            trace_writer.emit(
                run_id, "worker_end", worker.name,
                {"answer_length": len(final_answer)},
            )
        return {
            "worker_outputs": {worker.name: final_answer},
            "messages": [
                AIMessage(content=f"[{worker.name}] {final_answer}", name=worker.name)
            ],
            "audit_events": [{"event_type": "worker_end", "actor": worker.name}],
        }

    return finalize


def make_worker_node(
    worker: Worker,
    llm: BaseChatModel,
    tools: list[BaseTool],
    trace_writer: TraceWriter | None = None,
):
    """创建 worker 节点：内部跑 ReAct 循环（LLM 调工具直到给出最终答案）。"""

    def worker_react(state: TeamState) -> dict:
        run_id = state.get("run_id", "")
        if trace_writer:
            trace_writer.emit(run_id, "worker_start", worker.name)

        step = state["plan"][state["current_step"]]
        instruction = step["instruction"]
        tool_map = {t.name: t for t in tools}
        llm_with_tools = llm.bind_tools(tools) if tools else llm
        messages = [
            SystemMessage(content=worker.system_prompt),
            HumanMessage(content=instruction),
        ]
        final_answer = ""
        for _ in range(worker.max_iterations):
            response = llm_with_tools.invoke(messages)
            messages.append(response)
            tool_calls = getattr(response, "tool_calls", None)
            if not tool_calls:
                final_answer = response.content
                break
            for tc in tool_calls:
                tool = tool_map.get(tc["name"])
                if tool is None:
                    result = f"工具 {tc['name']} 不存在"
                else:
                    try:
                        result = tool.invoke(tc["args"])
                    except Exception as e:
                        result = f"工具执行出错：{type(e).__name__}: {e}"
                messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))
        else:
            final_answer = getattr(messages[-1], "content", "") if messages else ""

        if trace_writer:
            trace_writer.emit(
                run_id, "worker_end", worker.name, {"answer_length": len(final_answer)}
            )
        return {
            "worker_outputs": {worker.name: final_answer},
            "messages": [
                AIMessage(content=f"[{worker.name}] {final_answer}", name=worker.name)
            ],
            "audit_events": [{"event_type": "worker_end", "actor": worker.name}],
        }

    return worker_react


def make_leader_review_node(
    leader: Leader, llm: BaseChatModel, trace_writer: TraceWriter | None = None
):
    """创建 leader_review 节点：点评 worker 产出，标记步骤完成，推进 current_step。"""

    def leader_review(state: TeamState) -> dict:
        run_id = state.get("run_id", "")
        current = state["current_step"]
        plan = list(state["plan"])
        plan[current] = {**plan[current], "status": "done"}
        worker_name = plan[current]["worker"]
        outputs = state.get("worker_outputs", {})
        review_response = llm.invoke(
            [
                SystemMessage(content=leader.system_prompt),
                HumanMessage(
                    content=(
                        f"Worker {worker_name} 完成了步骤 {current}，"
                        f"产出：{outputs.get(worker_name, '')}。请简要点评。"
                    )
                ),
            ]
        )
        if trace_writer:
            trace_writer.emit(run_id, "leader_review", leader.name)
        return {
            "plan": plan,
            "current_step": current + 1,
            "messages": [
                AIMessage(content=f"[Leader] {review_response.content}", name=leader.name)
            ],
            "audit_events": [{"event_type": "leader_review", "actor": leader.name}],
        }

    return leader_review
