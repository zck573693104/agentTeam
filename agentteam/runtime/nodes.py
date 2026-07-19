from __future__ import annotations

from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from agentteam.domain.agent import Agent
from agentteam.domain.approval import ApprovalPolicy
from agentteam.runtime.errors import RunCancelledError
from agentteam.runtime.state import TeamState
from agentteam.runtime.trace import TraceWriter


class PlanStep(BaseModel):
    """计划中的一步：指派给某 worker 的子任务。

    dag 模式下:
    - id: 唯一标识(空=用 worker 名作 id),用于 depends_on 引用
    - depends_on: 依赖的 step id 列表(空=可立即执行)
    - condition: Python 表达式,求值 False 则跳过此步(None=不评估)
    """

    worker: str = Field(description="执行此步的 worker name")
    instruction: str = Field(description="子任务描述")
    id: str = Field(default="", description="唯一 id(空=用 worker 名)")
    depends_on: list[str] = Field(
        default_factory=list, description="依赖的 step id 列表"
    )
    condition: str | None = Field(
        default=None, description="Python 表达式,求值 False 则跳过"
    )


class Plan(BaseModel):
    """Leader 拆解出的执行计划。

    execution_mode:
    - sequential(默认): 沿用 current_step 线性推进,向后兼容
    - dag: 用 completed_steps/skipped_steps + 拓扑排序并行触发
    """

    steps: list[PlanStep] = Field(description="按顺序或 DAG 执行的步骤列表")
    execution_mode: Literal["sequential", "dag"] = Field(
        default="sequential", description="执行模式"
    )


def make_leader_plan_node(
    agent: Agent, llm: BaseChatModel, trace_writer: TraceWriter | None = None
):
    """创建 leader_plan 节点：用 LLM 结构化输出把 task 拆成 plan。

    dag 模式(execution_mode == "dag"):
    - 初始化 completed_steps=set()、skipped_steps=set()
    - 不写 current_step(dag 模式不用线性计数器)
    - 检测 plan 循环依赖,有环抛 ValueError
    sequential 模式:沿用 current_step=0(向后兼容)
    """
    from agentteam.runtime.graph import _detect_dag_cycle

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
        execution_mode = plan_obj.execution_mode

        plan = [
            {
                "worker": s.worker,
                "instruction": s.instruction,
                "status": "pending",
                "id": s.id or s.worker,
                "depends_on": list(s.depends_on),
                "condition": s.condition,
            }
            for s in plan_obj.steps
        ]

        # dag 模式:校验 step id 唯一性(避免 LLM 对同一 worker 产多步导致 id 冲突)
        if execution_mode == "dag":
            # 用 Counter 替代 ids.count(sid) 双层循环,O(n²) → O(n)
            from collections import Counter
            id_counts = Counter(s["id"] for s in plan)
            duplicates = {sid for sid, n in id_counts.items() if n > 1}
            if duplicates:
                raise ValueError(
                    f"Plan has duplicate step ids in dag mode: {sorted(duplicates)}. "
                    f"Use explicit unique 'id' for steps sharing the same worker."
                )

        # dag 模式:检测循环依赖
        if execution_mode == "dag" and _detect_dag_cycle(plan):
            raise ValueError(
                f"Plan has circular dependency in dag mode: "
                f"{[s['id'] for s in plan]}. Refusing to execute."
            )

        if trace_writer:
            trace_writer.emit(run_id, "leader_plan", agent.name, {"steps": len(plan)})

        result: dict = {
            "plan": plan,
            "execution_mode": execution_mode,
            "messages": [
                AIMessage(content=f"[Leader] 计划已拆解：{len(plan)} 步", name=agent.name)
            ],
            "audit_events": [{"event_type": "leader_plan", "actor": agent.name}],
        }
        if execution_mode == "dag":
            result["completed_steps"] = set()
            result["skipped_steps"] = set()
            # dag 模式不写 current_step
        else:
            result["current_step"] = 0
        return result

    return leader_plan


def make_init_worker(
    agent: Agent,
    trace_writer: TraceWriter | None = None,
    skills: dict[str, str] | None = None,
):
    """创建 init_worker 节点：初始化 ReAct 循环的 react_messages 和计数器。

    dag 模式:从 plan 中找到本 worker 的 ready step(id 不在 completed/skipped,
    worker 名匹配),设置 current_step_id。
    sequential 模式:沿用 plan[current_step],current_step_id 留空。

    SP7a: 若 skills 非空,把 skills 包装为 <skill> 标签的 SystemMessage,
    插入到 react_messages[1](system_prompt 之后、task 之前),
    让 LLM 先建立身份再接收行为指导,再处理任务。
    """

    def init_worker(state: TeamState) -> dict:
        run_id = state.get("run_id", "")
        execution_mode = state.get("execution_mode", "sequential")

        if execution_mode == "dag":
            # dag 模式:从 plan 找本 worker 的 ready step
            plan = state.get("plan", [])
            completed = state.get("completed_steps", set())
            skipped = state.get("skipped_steps", set())
            current_step_id = ""
            instruction = None
            for step in plan:
                sid = step.get("id") or step.get("worker")
                if sid in completed or sid in skipped:
                    continue
                if step.get("worker") == agent.name:
                    current_step_id = sid
                    instruction = step["instruction"]
                    break
            if instruction is None or not current_step_id:
                raise ValueError(
                    f"Worker {agent.name} has no ready step in plan "
                    f"(completed={sorted(completed)}, skipped={sorted(skipped)}). "
                    f"Router should not dispatch idle workers."
                )
        else:
            # sequential 模式:沿用 current_step
            step = state["plan"][state["current_step"]]
            instruction = step["instruction"]
            current_step_id = ""

        if trace_writer:
            trace_writer.emit(run_id, "worker_start", agent.name)

        # 构造 react_messages:[system_prompt] + (可选 skills) + [task]
        react_messages: list = [
            SystemMessage(content=agent.system_prompt),
        ]
        if skills:
            skill_text = "\n\n".join(
                f'<skill name="{name}">\n{content}\n</skill>'
                for name, content in skills.items()
            )
            react_messages.append(SystemMessage(content=skill_text))
        react_messages.append(HumanMessage(content=instruction))

        return {
            "react_messages": react_messages,
            "tool_calls": [],
            "iteration": 0,
            "final_answer": "",
            "current_step_id": current_step_id,
        }

    return init_worker


def make_agent_step(
    agent: Agent,
    llm: BaseChatModel,
    tools: list[BaseTool],
    run_manager=None,
):
    """创建 agent_step 节点：LLM 决策调用工具或给出最终答案。

    新增 run_manager 参数:若提供,在 LLM 调用前检查 run 是否被取消,
    命中则抛 RunCancelledError(继承 BaseException,绕过 worker 内 except Exception),
    避免浪费 LLM token。
    """

    llm_with_tools = llm.bind_tools(tools) if tools else llm

    def agent_step(state: dict) -> dict:
        if run_manager is not None:
            run_id = state.get("run_id", "")
            if run_manager.is_cancelled(run_id):
                raise RunCancelledError(f"Run {run_id} cancelled by user")
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
    """创建 finalize 节点：写 worker_outputs、汇总 messages、emit worker_end。

    dag 模式:额外回传 completed_steps={current_step_id},通过 set_union
    reducer 合并到父图 completed_steps(支持并行 worker)。
    sequential 模式:不回传 completed_steps。
    """

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
        result: dict = {
            "worker_outputs": {agent.name: final_answer},
            "messages": [
                AIMessage(content=f"[{agent.name}] {final_answer}", name=agent.name)
            ],
            "audit_events": [{"event_type": "worker_end", "actor": agent.name}],
        }
        # dag 模式:回传 completed_steps(set,经 set_union reducer 合并到父图)
        if state.get("execution_mode") == "dag":
            current_step_id = state.get("current_step_id", "")
            if current_step_id:
                result["completed_steps"] = {current_step_id}
        return result

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
    run_manager=None,
    skills: dict[str, str] | None = None,
):
    """编译 Worker ReAct 子图：init_worker → agent_step → tool_step → 循环 → finalize。

    返回 compiled subgraph，可直接作为父图的节点。
    新增 run_manager 参数:透传给 make_agent_step,使 worker 能检查取消信号。
    新增 skills 参数(SP7a):透传给 make_init_worker,注入到 react_messages。
    """
    from langgraph.graph import END, START, StateGraph
    from agentteam.runtime.state import WorkerState

    approval_policy = agent.approval_policy

    sg = StateGraph(WorkerState)
    sg.add_node("init_worker", make_init_worker(agent, trace_writer, skills=skills))
    sg.add_node("agent_step", make_agent_step(agent, llm, tools, run_manager=run_manager))
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
    run_manager=None,
    skills: dict[str, str] | None = None,
):
    """返回可调用节点函数，内部使用子图。

    剥离共享累加器字段（messages/audit_events/worker_outputs）后传入子图，
    避免子图 reducer 与父图 reducer 双重累积导致重复。
    透传 config 以支持子图内 interrupt/resume（工具级审批）。

    输出过滤:dag 模式下多个 worker 并行触发,子图回传的 plan/current_step 等
    LastValue 通道会并发写入冲突(InvalidUpdateError)。因此只回传累加器
    (有 reducer) + dag 模式 completed_steps + 审批信号,其余字段由父图自管。
    新增 run_manager 参数:透传给 make_worker_subgraph,使 worker 能检查取消信号。
    新增 skills 参数(SP7a):透传给 make_worker_subgraph,注入到 react_messages。
    """
    subgraph = make_worker_subgraph(
        agent, llm, tools, trace_writer, audit_repo,
        run_manager=run_manager, skills=skills,
    )

    # 共享累加器字段：子图不需要读取它们（只用 react_messages 内部通信），
    # 但若传入，子图的 reducer 会累积它们，返回时父图 reducer 再次累积 → 重复。
    # 因此从输入中剥离，让子图只产出自己的增量。
    _ACCUMULATOR_KEYS = frozenset({"messages", "audit_events", "worker_outputs", "total_tokens"})
    # 只回传这些 key:累加器(有 reducer) + dag completed_steps(set_union) + 审批信号。
    # plan/current_step/execution_mode 等 LastValue 通道不回传,避免并行 worker 冲突。
    _RETURN_KEYS = frozenset({
        "messages", "audit_events", "worker_outputs", "total_tokens",
        "completed_steps",  # dag 模式:worker 完成后回传 {current_step_id}
        "pending_approval",  # 审批中断信号需冒泡到父图
    })

    def worker_node(state: TeamState, config=None) -> dict:
        subgraph_input = {
            k: v for k, v in state.items() if k not in _ACCUMULATOR_KEYS
        }
        if config is not None:
            sub_result = subgraph.invoke(subgraph_input, config)
        else:
            sub_result = subgraph.invoke(subgraph_input)
        return {k: v for k, v in sub_result.items() if k in _RETURN_KEYS}

    return worker_node


def make_supervisor_node(compiled_graph, agent_name: str):
    """包装 compiled supervisor 子图，隔离其编排状态与父图。

    supervisor 子图作为父图的子节点时：
    - 输入：从父图 plan[current_step].instruction 提取子任务作为子图 task；
      剥离 plan/current_step/path/累加器字段，子图从空白开始编排。
    - 输出：只回传累加器增量（messages/audit_events/worker_outputs/total_tokens）
      与 pending_approval（审批中断信号需冒泡）；不回传 plan/current_step，
      避免覆盖父图状态机。

    透传 config 以支持子图内 interrupt/resume（step 级审批）。
    """
    _STRIP_FROM_INPUT = frozenset({
        "plan", "current_step", "path",  # 编排字段：子图自行生成
        "messages", "audit_events", "worker_outputs", "total_tokens",  # 累加器：从空开始
    })
    _RETURN_KEYS = frozenset({
        "messages", "audit_events", "worker_outputs", "total_tokens",
        "pending_approval",  # 审批中断信号需冒泡到父图
    })

    def supervisor_node(state: TeamState, config=None) -> dict:
        # 从父图 plan 取出本步的 instruction 作为子图 task
        current = state.get("current_step", 0)
        plan = state.get("plan", [])
        if current < len(plan):
            instruction = plan[current].get("instruction", state.get("task", ""))
        else:
            instruction = state.get("task", "")

        subgraph_input = {
            k: v for k, v in state.items() if k not in _STRIP_FROM_INPUT
        }
        subgraph_input["task"] = instruction
        subgraph_input["plan"] = []
        subgraph_input["current_step"] = 0
        subgraph_input["path"] = f"{state.get('path', '')}.{agent_name}"
        # 累加器给空初始值
        subgraph_input["messages"] = []
        subgraph_input["audit_events"] = []
        subgraph_input["worker_outputs"] = {}
        subgraph_input["total_tokens"] = 0

        if config is not None:
            sub_result = compiled_graph.invoke(subgraph_input, config)
        else:
            sub_result = compiled_graph.invoke(subgraph_input)

        # 只回传累加器增量 + 审批信号；不回传 plan/current_step/path
        return {
            k: sub_result.get(k, [] if k != "total_tokens" else 0)
            for k in _RETURN_KEYS
        }

    return supervisor_node


def make_leader_review_node(
    agent: Agent, llm: BaseChatModel, trace_writer: TraceWriter | None = None
):
    """创建 leader_review 节点：点评 worker 产出。

    dag 模式:
    - completed_steps 已由 worker 通过 set_union reducer 更新,leader_review 不覆盖
    - 不推进 current_step(dag 模式不用)
    - 仅做 LLM 点评 + emit trace
    sequential 模式:沿用 current_step += 1 + 标记 plan[current] done(向后兼容)
    """

    def leader_review(state: TeamState) -> dict:
        run_id = state.get("run_id", "")
        execution_mode = state.get("execution_mode", "sequential")

        if execution_mode == "dag":
            # dag 模式:completed_steps 已由 worker reducer 更新
            # leader_review 只需 LLM 点评,不推进 current_step,不覆盖 completed_steps
            outputs = state.get("worker_outputs", {})
            # 取最近完成的 worker(任取一个用于点评)
            recent_worker = next(iter(outputs), "")
            review_response = llm.invoke(
                [
                    SystemMessage(content=agent.system_prompt),
                    HumanMessage(
                        content=(
                            f"Worker {recent_worker} 完成了步骤，"
                            f"产出：{outputs.get(recent_worker, '')}。请简要点评。"
                        )
                    ),
                ]
            )
            if trace_writer:
                trace_writer.emit(run_id, "leader_review", agent.name)
            usage = getattr(review_response, "usage_metadata", None)
            tokens = usage.get("total_tokens", 0) if usage else 0
            return {
                "messages": [
                    AIMessage(content=f"[Leader] {review_response.content}", name=agent.name)
                ],
                "audit_events": [{"event_type": "leader_review", "actor": agent.name}],
                "total_tokens": tokens,
            }

        # sequential 模式:沿用原逻辑
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
