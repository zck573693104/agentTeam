from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from agentteam.domain.agent import Agent
from agentteam.domain.approval import ApprovalPolicy
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
    agent: Agent, llm: BaseChatModel, trace_writer: TraceWriter | None = None
):
    """创建 leader_plan 节点：用 LLM 结构化输出把 task 拆成 plan。"""

    def leader_plan(state: TeamState) -> dict:
        run_id = state.get("run_id", "")
        task = state["task"]
        messages = [
            SystemMessage(content=agent.system_prompt),
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
            trace_writer.emit(run_id, "leader_plan", agent.name, {"steps": len(plan)})
        return {
            "plan": plan,
            "current_step": 0,
            "messages": [
                AIMessage(content=f"[Leader] 计划已拆解：{len(plan)} 步", name=agent.name)
            ],
            "audit_events": [{"event_type": "leader_plan", "actor": agent.name}],
        }

    return leader_plan


def make_init_worker(
    agent: Agent,
    trace_writer: TraceWriter | None = None,
):
    """创建 init_worker 节点：初始化 ReAct 循环的 react_messages 和计数器。"""

    def init_worker(state: TeamState) -> dict:
        run_id = state.get("run_id", "")
        step = state["plan"][state["current_step"]]
        instruction = step["instruction"]

        if trace_writer:
            trace_writer.emit(run_id, "worker_start", agent.name)

        return {
            "react_messages": [
                SystemMessage(content=agent.system_prompt),
                HumanMessage(content=instruction),
            ],
            "tool_calls": [],
            "iteration": 0,
            "final_answer": "",
        }

    return init_worker


def make_agent_step(
    agent: Agent,
    llm: BaseChatModel,
    tools: list[BaseTool],
):
    """创建 agent_step 节点：LLM 决策调用工具或给出最终答案。"""

    llm_with_tools = llm.bind_tools(tools) if tools else llm

    def agent_step(state: dict) -> dict:
        react_messages = state.get("react_messages", [])
        response = llm_with_tools.invoke(react_messages)

        usage = getattr(response, "usage_metadata", None)
        tokens = usage.get("total_tokens", 0) if usage else 0

        tool_calls = getattr(response, "tool_calls", None)
        if tool_calls:
            return {
                "react_messages": [response],
                "tool_calls": tool_calls,
                "final_answer": "",
                "total_tokens": tokens,
            }
        return {
            "react_messages": [response],
            "tool_calls": [],
            "final_answer": response.content,
            "total_tokens": tokens,
        }

    return agent_step


def make_finalize(
    agent: Agent,
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
                run_id, "worker_end", agent.name,
                {"answer_length": len(final_answer)},
            )
        return {
            "worker_outputs": {agent.name: final_answer},
            "messages": [
                AIMessage(content=f"[{agent.name}] {final_answer}", name=agent.name)
            ],
            "audit_events": [{"event_type": "worker_end", "actor": agent.name}],
        }

    return finalize


def make_tool_step(
    agent: Agent,
    tools: list[BaseTool],
    approval_policy: ApprovalPolicy | None = None,
    trace_writer: TraceWriter | None = None,
    audit_repo=None,
):
    """创建 tool_step 节点：检查工具级审批 → interrupt → 执行工具 → 回灌结果。

    审批按批次：批次中任一工具匹配 targets 则触发一次 interrupt。
    所有副作用（DB 写、trace、工具执行）放在 interrupt() 之后。
    """
    from langgraph.types import interrupt
    from agentteam.runtime.approval import _should_approve

    tool_map = {t.name: t for t in tools}

    def tool_step(state: dict) -> dict:
        run_id = state.get("run_id", "")
        tool_calls = state.get("tool_calls", [])
        iteration = state.get("iteration", 0)
        new_messages = []

        # 检查是否需要工具级审批
        needs_approval = (
            approval_policy is not None
            and approval_policy.level == "tool"
            and any(_should_approve(approval_policy, tc["name"]) for tc in tool_calls)
        )

        if needs_approval:
            decision = interrupt({
                "gate": "tool",
                "worker": agent.name,
                "tool_calls": [{"name": tc["name"], "args": tc["args"]} for tc in tool_calls],
                "message": f"Worker {agent.name} 请求调用工具: {[tc['name'] for tc in tool_calls]}",
            })
            approved = decision.get("approved", False)
            decider = decision.get("decider", "unknown")

            # 副作用在 interrupt 之后
            if audit_repo is not None:
                approval_id = audit_repo.add_approval(run_id)
                audit_repo.decide_approval(
                    approval_id, "approved" if approved else "rejected", decider
                )
            if trace_writer is not None:
                trace_writer.emit(
                    run_id, "approval_requested", "system",
                    {"gate": "tool", "worker": agent.name,
                     "tools": [tc["name"] for tc in tool_calls]},
                )
                trace_writer.emit(
                    run_id, "approval_decided", decider,
                    {"gate": "tool", "approved": approved},
                )

            if not approved:
                for tc in tool_calls:
                    new_messages.append(
                        ToolMessage(content="工具调用已被拒绝", tool_call_id=tc["id"])
                    )
                return {
                    "react_messages": new_messages,
                    "tool_calls": [],
                    "iteration": iteration + 1,
                }

        # 执行工具
        if trace_writer is not None:
            trace_writer.emit(
                run_id, "tool_call", agent.name,
                {"tools": [tc["name"] for tc in tool_calls]},
            )

        for tc in tool_calls:
            tool = tool_map.get(tc["name"])
            if tool is None:
                result = f"工具 {tc['name']} 不存在"
            else:
                try:
                    result = tool.invoke(tc["args"])
                except Exception as e:
                    result = f"工具执行出错：{type(e).__name__}: {e}"
            new_messages.append(
                ToolMessage(content=str(result), tool_call_id=tc["id"])
            )

        return {
            "react_messages": new_messages,
            "tool_calls": [],
            "iteration": iteration + 1,
        }

    return tool_step


def make_worker_subgraph(
    agent: Agent,
    llm: BaseChatModel,
    tools: list[BaseTool],
    trace_writer: TraceWriter | None = None,
    audit_repo=None,
):
    """编译 Worker ReAct 子图：init_worker → agent_step → tool_step → 循环 → finalize。

    返回 compiled subgraph，可直接作为父图的节点。
    """
    from langgraph.graph import END, START, StateGraph
    from agentteam.runtime.state import WorkerState

    approval_policy = agent.approval_policy

    sg = StateGraph(WorkerState)
    sg.add_node("init_worker", make_init_worker(agent, trace_writer))
    sg.add_node("agent_step", make_agent_step(agent, llm, tools))
    sg.add_node(
        "tool_step",
        make_tool_step(agent, tools, approval_policy, trace_writer, audit_repo),
    )
    sg.add_node("finalize", make_finalize(agent, trace_writer))

    # 边
    sg.add_edge(START, "init_worker")
    sg.add_edge("init_worker", "agent_step")

    # agent_step → tool_step（有 tool_calls）或 finalize（无 tool_calls）
    def route_after_agent(state: dict) -> str:
        if state.get("final_answer"):
            return "finalize"
        if not state.get("tool_calls"):
            return "finalize"
        return "tool_step"

    sg.add_conditional_edges("agent_step", route_after_agent)

    # tool_step → agent_step（未达上限）或 finalize（达上限）
    max_iter = agent.max_iterations

    def route_after_tool(state: dict) -> str:
        if state.get("iteration", 0) >= max_iter:
            return "finalize"
        return "agent_step"

    sg.add_conditional_edges("tool_step", route_after_tool)
    sg.add_edge("finalize", END)

    return sg.compile()


def make_worker_node(
    agent: Agent,
    llm: BaseChatModel,
    tools: list[BaseTool],
    trace_writer: TraceWriter | None = None,
    audit_repo=None,
):
    """返回可调用节点函数，内部使用子图。

    剥离共享累加器字段（messages/audit_events/worker_outputs）后传入子图，
    避免子图 reducer 与父图 reducer 双重累积导致重复。
    透传 config 以支持子图内 interrupt/resume（工具级审批）。
    """
    subgraph = make_worker_subgraph(agent, llm, tools, trace_writer, audit_repo)

    # 共享累加器字段：子图不需要读取它们（只用 react_messages 内部通信），
    # 但若传入，子图的 reducer 会累积它们，返回时父图 reducer 再次累积 → 重复。
    # 因此从输入中剥离，让子图只产出自己的增量。
    _ACCUMULATOR_KEYS = frozenset({"messages", "audit_events", "worker_outputs", "total_tokens"})

    def worker_node(state: TeamState, config=None) -> dict:
        subgraph_input = {
            k: v for k, v in state.items() if k not in _ACCUMULATOR_KEYS
        }
        if config is not None:
            return subgraph.invoke(subgraph_input, config)
        return subgraph.invoke(subgraph_input)

    return worker_node


def make_leader_review_node(
    agent: Agent, llm: BaseChatModel, trace_writer: TraceWriter | None = None
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
                SystemMessage(content=agent.system_prompt),
                HumanMessage(
                    content=(
                        f"Worker {worker_name} 完成了步骤 {current}，"
                        f"产出：{outputs.get(worker_name, '')}。请简要点评。"
                    )
                ),
            ]
        )
        if trace_writer:
            trace_writer.emit(run_id, "leader_review", agent.name)
        usage = getattr(review_response, "usage_metadata", None)
        tokens = usage.get("total_tokens", 0) if usage else 0
        return {
            "plan": plan,
            "current_step": current + 1,
            "messages": [
                AIMessage(content=f"[Leader] {review_response.content}", name=agent.name)
            ],
            "audit_events": [{"event_type": "leader_review", "actor": agent.name}],
            "total_tokens": tokens,
        }

    return leader_review
