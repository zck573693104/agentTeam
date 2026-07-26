from __future__ import annotations

from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from agentteam.domain.agent import Agent
from agentteam.logging_config import get_logger
from agentteam.runtime.errors import RunCancelledError
from agentteam.runtime.state import TeamState
from agentteam.runtime.trace import TraceWriter

logger = get_logger("runtime.nodes")


class PlanStep(BaseModel):
    """计划中的一步：指派给某 worker 的子任务。

    dag 模式下:
    - id: 唯一标识(空=用 worker 名作 id),用于 depends_on 引用
    - depends_on: 依赖的 step id 列表(空=可立即执行)
    - condition: Python 表达式,求值 False 则跳过此步(None=不评估)

    Graph Engineering P2 节点契约:
    - acceptance_criteria: 可机器/人工验证的验收标准(如"测试通过""输出含 X 字段"),
      leader_review 据此判断 worker 产出是否合格,而非凭 LLM 自由判断
    - budget_tokens: 单步 token 预算上限(超限触发警告,0=不限)
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
    # Graph Engineering P2 节点契约字段
    acceptance_criteria: str | None = Field(
        default=None,
        description="可验证的验收标准(leader_review 据此判断产出是否合格)",
    )
    budget_tokens: int = Field(
        default=0,
        description="单步 token 预算上限(0=不限,超限触发警告)",
    )


class WorkerOutput(BaseModel):
    """Graph Engineering P2: Worker 产出契约。

    节点输出分四类(对标文章"避免把一段自然语言丢给下游猜"):
    - artifact: 实际产物(代码/报告/数据),主输出
    - evidence: 支撑证据(测试结果/引用/日志),用于 leader_review 验收
    - state_delta: 状态变更(如新增文件列表),供下游 step 引用
    - failure: 失败原因(None=成功),非空时 leader_review 直接判不合格
    """

    artifact: str = Field(description="实际产物(代码/报告/数据)")
    evidence: list[str] = Field(
        default_factory=list, description="支撑证据(测试结果/引用/日志)"
    )
    state_delta: dict = Field(
        default_factory=dict, description="状态变更(如新增文件列表)"
    )
    failure: str | None = Field(
        default=None, description="失败原因(None=成功)"
    )

    @classmethod
    def from_text(cls, text: str) -> "WorkerOutput":
        """从纯文本构造(向后兼容:无结构化信息时,整体作为 artifact)。

        旧 worker 只返回 str,Leader review 时仍能工作。
        """
        return cls(artifact=text)

    def to_dict(self) -> dict:
        """序列化为 dict(存入 worker_outputs 字段时用)。"""
        return {
            "artifact": self.artifact,
            "evidence": list(self.evidence),
            "state_delta": dict(self.state_delta),
            "failure": self.failure,
        }

    @classmethod
    def from_dict(cls, d: dict | str) -> "WorkerOutput":
        """从 dict 或 str 反序列化(str 视为纯 artifact,向后兼容)。"""
        if isinstance(d, str):
            return cls(artifact=d)
        return cls(
            artifact=d.get("artifact", ""),
            evidence=list(d.get("evidence", [])),
            state_delta=dict(d.get("state_delta", {})),
            failure=d.get("failure"),
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
                # Graph Engineering P2 节点契约字段透传
                "acceptance_criteria": s.acceptance_criteria,
                "budget_tokens": s.budget_tokens,
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

    Graph Engineering P2 节点契约:worker_outputs 存结构化 WorkerOutput dict
    (artifact/evidence/state_delta/failure),向后兼容旧 leader_review
    (旧的 str 输出经 WorkerOutput.from_text 转为 {artifact: str})。
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

        # Graph Engineering P2: 结构化 worker 产出
        # 当前 worker 仅产出文本,作为 artifact;evidence/state_delta/failure 留空
        # 后续可让 worker 主动产出结构化 evidence(如测试结果)
        worker_output = WorkerOutput(artifact=final_answer)

        if trace_writer:
            trace_writer.emit(
                run_id, "worker_end", agent.name,
                {
                    "answer_length": len(final_answer),
                    "has_evidence": bool(worker_output.evidence),
                    "failure": worker_output.failure,
                },
                state_bucket="artifact",
            )
        result: dict = {
            "worker_outputs": {agent.name: worker_output.to_dict()},
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
    trace_writer: TraceWriter | None = None,
    pipeline=None,
):
    """创建 tool_step 节点：执行 ToolCallPipeline → 回灌 ToolMessage。

    架构调整(借鉴 pi-mono):原 make_tool_step 内嵌 PEP 拦截 + 审批 + 执行
    三件事,现解耦为可插拔 ToolCallPipeline。core 仅提供 pipeline 框架
    和默认 ExecutionStage;审批/PEP 等成品功能通过 stage 或 hooks 注入。

    Args:
        agent: 执行此步的 worker agent。
        tools: 可用工具列表。
        trace_writer: 轨迹写入器(可选)。
        pipeline: 预构建的 ToolCallPipeline(可选)。None 时用默认 pipeline
            (仅 ExecutionStage)。调用方可通过 pipeline.add_stage 注入自定义 stage。
    """
    from agentteam.runtime.tool_pipeline import (
        ToolCallContext,
        ToolCallPipeline,
        build_default_pipeline,
    )
    from agentteam.runtime.hooks import get_hooks

    tool_map = {t.name: t for t in tools}
    active_pipeline = pipeline or build_default_pipeline(tool_map)
    hooks = get_hooks()

    def tool_step(state: dict) -> dict:
        run_id = state.get("run_id", "")
        tool_calls = state.get("tool_calls", [])
        iteration = state.get("iteration", 0)
        new_messages = []

        if trace_writer is not None and tool_calls:
            trace_writer.emit(
                run_id, "tool_call", agent.name,
                {"tools": [tc["name"] for tc in tool_calls]},
            )

        for tc in tool_calls:
            # 构建 pipeline 上下文
            ctx = ToolCallContext(
                run_id=run_id,
                agent_name=agent.name,
                tool_call=tc,
                state=dict(state),
            )
            # pre_tool_call 钩子(对标 pi-mono beforeToolCall)
            # 钩子可往 ctx.metadata 写决策信息,供后续 stage 读取
            hooks.emit("pre_tool_call", {
                "run_id": run_id,
                "agent_name": agent.name,
                "tool_call": tc,
                "ctx": ctx,
            })
            # 执行 pipeline
            result = active_pipeline.execute(ctx)
            new_messages.append(
                ToolMessage(
                    content=result.content,
                    tool_call_id=result.tool_call_id,
                )
            )
            # post_tool_call 钩子(对标 pi-mono afterToolCall)
            hooks.emit("post_tool_call", {
                "run_id": run_id,
                "agent_name": agent.name,
                "tool_call": tc,
                "result": result,
            })

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
    pipeline=None,
):
    """编译 Worker ReAct 子图：init_worker → agent_step → tool_step → 循环 → finalize。

    返回 compiled subgraph，可直接作为父图的节点。
    新增 run_manager 参数:透传给 make_agent_step,使 worker 能检查取消信号。
    新增 skills 参数(SP7a):透传给 make_init_worker,注入到 react_messages。
    新增 pipeline 参数(架构调整):透传给 make_tool_step,可插拔工具调用流水线。
    """
    from langgraph.graph import END, START, StateGraph
    from agentteam.runtime.state import WorkerState

    sg = StateGraph(WorkerState)
    sg.add_node("init_worker", make_init_worker(agent, trace_writer, skills=skills))
    sg.add_node("agent_step", make_agent_step(agent, llm, tools, run_manager=run_manager))
    sg.add_node(
        "tool_step",
        make_tool_step(agent, tools, trace_writer, pipeline=pipeline),
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
    pipeline=None,
):
    """返回可调用节点函数，内部使用子图。

    剥离共享累加器字段（messages/audit_events/worker_outputs）后传入子图，
    避免子图 reducer 与父图 reducer 双重累积导致重复。
    透传 config 以支持子图内 interrupt/resume。

    输出过滤:dag 模式下多个 worker 并行触发,子图回传的 plan/current_step 等
    LastValue 通道会并发写入冲突(InvalidUpdateError)。因此只回传累加器
    (有 reducer) + dag 模式 completed_steps,其余字段由父图自管。
    新增 run_manager 参数:透传给 make_worker_subgraph,使 worker 能检查取消信号。
    新增 skills 参数(SP7a):透传给 make_worker_subgraph,注入到 react_messages。
    新增 pipeline 参数(架构调整):透传给 make_worker_subgraph,可插拔工具流水线。
    """
    subgraph = make_worker_subgraph(
        agent, llm, tools, trace_writer, audit_repo,
        run_manager=run_manager, skills=skills, pipeline=pipeline,
    )

    # 共享累加器字段：子图不需要读取它们（只用 react_messages 内部通信），
    # 但若传入，子图的 reducer 会累积它们，返回时父图 reducer 再次累积 → 重复。
    # 因此从输入中剥离，让子图只产出自己的增量。
    _ACCUMULATOR_KEYS = frozenset({"messages", "audit_events", "worker_outputs", "total_tokens"})
    # 只回传这些 key:累加器(有 reducer) + dag completed_steps(set_union)。
    _RETURN_KEYS = frozenset({
        "messages", "audit_events", "worker_outputs", "total_tokens",
        "completed_steps",  # dag 模式:worker 完成后回传 {current_step_id}
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
    agent: Agent, llm: BaseChatModel, trace_writer: TraceWriter | None = None,
    review_llm: BaseChatModel | None = None,
):
    """创建 leader_review 节点：点评 worker 产出。

    dag 模式:
    - completed_steps 已由 worker 通过 set_union reducer 更新,leader_review 不覆盖
    - 不推进 current_step(dag 模式不用)
    - 仅做 LLM 点评 + emit trace
    sequential 模式:沿用 current_step += 1 + 标记 plan[current] done(向后兼容)

    Graph Engineering P2 节点契约:
    - 对照 plan step 的 acceptance_criteria 验收 worker 产出
    - worker_outputs 现为结构化 WorkerOutput dict(artifact/evidence/state_delta/failure)
    - failure 非空直接判不合格;acceptance_criteria 存在时把它喂给 LLM 评估

    Graph Engineering P3 Maker/Checker 独立性:
    - review_llm 不为 None 时,使用独立模型做 review(避免 maker/checker 同模型自欺)
    - review_llm 为 None 时回退到 llm(向后兼容)
    """

    def leader_review(state: TeamState) -> dict:
        run_id = state.get("run_id", "")
        execution_mode = state.get("execution_mode", "sequential")
        # P3: 优先用独立 review_llm,避免 maker/checker 同模型
        actual_llm = review_llm if review_llm is not None else llm

        if execution_mode == "dag":
            # dag 模式:completed_steps 已由 worker reducer 更新
            # leader_review 只需 LLM 点评,不推进 current_step,不覆盖 completed_steps
            outputs = state.get("worker_outputs", {})
            plan = state.get("plan", [])
            # 取最近完成的 worker(任取一个用于点评)
            recent_worker = next(iter(outputs), "")
            # P2: 找到对应 step 的 acceptance_criteria
            acceptance_criteria = None
            for step in plan:
                sid = step.get("id") or step.get("worker")
                if sid == recent_worker or step.get("worker") == recent_worker:
                    acceptance_criteria = step.get("acceptance_criteria")
                    break
            # P2: 解析结构化 worker 产出
            raw_output = outputs.get(recent_worker, "")
            worker_output = WorkerOutput.from_dict(raw_output)

            # P2: failure 非空直接判不合格,跳过 LLM 调用节省 token
            if worker_output.failure:
                review_text = f"[自动验收] Worker {recent_worker} 报告失败: {worker_output.failure}"
                if trace_writer:
                    trace_writer.emit(
                        run_id, "leader_review", agent.name,
                        {"auto_verdict": "failed", "failure": worker_output.failure},
                        state_bucket="artifact",
                    )
                return {
                    "messages": [
                        AIMessage(content=f"[Leader] {review_text}", name=agent.name)
                    ],
                    "audit_events": [{"event_type": "leader_review", "actor": agent.name}],
                }

            # P2: 构造验收 prompt(若有 acceptance_criteria)
            criteria_hint = ""
            if acceptance_criteria:
                criteria_hint = (
                    f"\n验收标准: {acceptance_criteria}\n"
                    f"请对照验收标准判断 worker 产出是否合格,并说明理由。\n"
                )
            evidence_hint = ""
            if worker_output.evidence:
                evidence_hint = (
                    f"\nWorker 提供的证据: {worker_output.evidence}\n"
                )

            review_response = actual_llm.invoke(
                [
                    SystemMessage(content=agent.system_prompt),
                    HumanMessage(
                        content=(
                            f"Worker {recent_worker} 完成了步骤，"
                            f"产出: {worker_output.artifact}"
                            f"{evidence_hint}{criteria_hint}"
                            f"请简要点评。"
                        )
                    ),
                ]
            )
            if trace_writer:
                trace_writer.emit(
                    run_id, "leader_review", agent.name,
                    {"has_criteria": bool(acceptance_criteria)},
                    state_bucket="artifact",
                )
            usage = getattr(review_response, "usage_metadata", None)
            tokens = usage.get("total_tokens", 0) if usage else 0
            return {
                "messages": [
                    AIMessage(content=f"[Leader] {review_response.content}", name=agent.name)
                ],
                "audit_events": [{"event_type": "leader_review", "actor": agent.name}],
                "total_tokens": tokens,
            }

        # sequential 模式:沿用原逻辑 + P2 节点契约
        current = state["current_step"]
        plan = list(state["plan"])
        plan[current] = {**plan[current], "status": "done"}
        worker_name = plan[current]["worker"]
        acceptance_criteria = plan[current].get("acceptance_criteria")
        outputs = state.get("worker_outputs", {})
        raw_output = outputs.get(worker_name, "")
        worker_output = WorkerOutput.from_dict(raw_output)

        # P2: failure 非空直接判不合格
        if worker_output.failure:
            review_text = f"[自动验收] Worker {worker_name} 报告失败: {worker_output.failure}"
            if trace_writer:
                trace_writer.emit(
                    run_id, "leader_review", agent.name,
                    {"auto_verdict": "failed", "failure": worker_output.failure},
                    state_bucket="artifact",
                )
            return {
                "plan": plan,
                "current_step": current + 1,
                "messages": [
                    AIMessage(content=f"[Leader] {review_text}", name=agent.name)
                ],
                "audit_events": [{"event_type": "leader_review", "actor": agent.name}],
            }

        criteria_hint = ""
        if acceptance_criteria:
            criteria_hint = (
                f"\n验收标准: {acceptance_criteria}\n"
                f"请对照验收标准判断 worker 产出是否合格,并说明理由。\n"
            )
        evidence_hint = ""
        if worker_output.evidence:
            evidence_hint = f"\nWorker 提供的证据: {worker_output.evidence}\n"

        review_response = actual_llm.invoke(
            [
                SystemMessage(content=agent.system_prompt),
                HumanMessage(
                    content=(
                        f"Worker {worker_name} 完成了步骤 {current}，"
                        f"产出: {worker_output.artifact}"
                        f"{evidence_hint}{criteria_hint}"
                        f"请简要点评。"
                    )
                ),
            ]
        )
        if trace_writer:
            trace_writer.emit(
                run_id, "leader_review", agent.name,
                {"has_criteria": bool(acceptance_criteria)},
                state_bucket="artifact",
            )
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
