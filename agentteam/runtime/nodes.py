from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agentteam.domain.team import Leader
from agentteam.runtime.state import TeamState


class PlanStep(BaseModel):
    """计划中的一步：指派给某 worker 的子任务。"""

    worker: str = Field(description="执行此步的 worker name")
    instruction: str = Field(description="子任务描述")


class Plan(BaseModel):
    """Leader 拆解出的执行计划。"""

    steps: list[PlanStep] = Field(description="按顺序执行的步骤列表")


def make_leader_plan_node(leader: Leader, llm: BaseChatModel):
    """创建 leader_plan 节点：用 LLM 结构化输出把 task 拆成 plan。"""

    def leader_plan(state: TeamState) -> dict:
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
        return {
            "plan": plan,
            "current_step": 0,
            "messages": [
                AIMessage(content=f"[Leader] 计划已拆解：{len(plan)} 步", name=leader.name)
            ],
            "audit_events": [{"event_type": "leader_plan", "actor": leader.name}],
        }

    return leader_plan
