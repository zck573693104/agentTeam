from langchain_core.messages import AIMessage

from agentteam.domain.team import Leader
from agentteam.domain.worker import Worker
from agentteam.models.provider import ModelRef
from agentteam.runtime.nodes import (
    Plan,
    PlanStep,
    make_leader_plan_node,
    make_leader_review_node,
    make_worker_node,
)


def test_leader_plan_produces_plan(fake_llm):
    plan = Plan(steps=[
        PlanStep(worker="coder", instruction="写代码"),
        PlanStep(worker="tester", instruction="写测试"),
    ])
    fake_llm.set_structured_responses([plan])

    leader = Leader(system_prompt="你是主管", model=ModelRef("qwen", "qwen-max"))
    node = make_leader_plan_node(leader, fake_llm)

    state = {"task": "开发 hello world", "messages": []}
    result = node(state)

    assert len(result["plan"]) == 2
    assert result["plan"][0]["worker"] == "coder"
    assert result["plan"][0]["instruction"] == "写代码"
    assert result["plan"][0]["status"] == "pending"
    assert result["current_step"] == 0
    assert len(result["messages"]) == 1
    assert len(result["audit_events"]) == 1


def test_leader_plan_empty_plan(fake_llm):
    fake_llm.set_structured_responses([Plan(steps=[])])

    leader = Leader(system_prompt="你是主管")
    node = make_leader_plan_node(leader, fake_llm)

    result = node({"task": "啥也不用做", "messages": []})
    assert result["plan"] == []
    assert result["current_step"] == 0


def test_worker_node_direct_answer(fake_llm):
    fake_llm.set_invoke_responses([AIMessage(content="hello world")])

    worker = Worker(
        name="coder",
        role="代码工程师",
        description="写代码",
        system_prompt="你是代码工程师",
    )
    node = make_worker_node(worker, fake_llm, [])

    state = {
        "plan": [{"worker": "coder", "instruction": "写 hello", "status": "pending"}],
        "current_step": 0,
    }
    result = node(state)

    assert result["worker_outputs"] == {"coder": "hello world"}
    assert len(result["messages"]) == 1
    assert "coder" in result["messages"][0].content


def test_worker_node_react_with_tool(fake_llm, tmp_path):
    from agentteam.tools.skills.file_ops import write_file

    target = tmp_path / "out.txt"
    fake_llm.set_invoke_responses([
        AIMessage(
            content="",
            tool_calls=[{
                "name": "write_file",
                "args": {"path": str(target), "content": "hi"},
                "id": "tc1",
            }],
        ),
        AIMessage(content="已写入文件"),
    ])

    worker = Worker(
        name="coder",
        role="代码工程师",
        description="写代码",
        system_prompt="你是代码工程师",
        tools=["write_file"],
    )
    node = make_worker_node(worker, fake_llm, [write_file])

    state = {
        "plan": [{"worker": "coder", "instruction": "写文件", "status": "pending"}],
        "current_step": 0,
    }
    result = node(state)

    assert target.read_text(encoding="utf-8") == "hi"
    assert result["worker_outputs"] == {"coder": "已写入文件"}


def test_worker_node_respects_max_iterations(fake_llm):
    # LLM 始终返回 tool_call，永不给最终答案
    fake_llm.set_invoke_responses([
        AIMessage(content="", tool_calls=[{
            "name": "read_file",
            "args": {"path": "x"},
            "id": f"tc{i}",
        }]) for i in range(3)
    ])

    from agentteam.tools.skills.file_ops import read_file

    worker = Worker(
        name="coder",
        role="代码工程师",
        description="写代码",
        system_prompt="你是代码工程师",
        tools=["read_file"],
        max_iterations=3,
    )
    node = make_worker_node(worker, fake_llm, [read_file])

    state = {
        "plan": [{"worker": "coder", "instruction": "读文件", "status": "pending"}],
        "current_step": 0,
    }
    result = node(state)

    # 达到 max_iterations 后强制结束，worker_outputs 仍有值
    assert "coder" in result["worker_outputs"]


def test_leader_review_marks_step_done_and_advances(fake_llm):
    fake_llm.set_invoke_responses([AIMessage(content="做得好")])

    leader = Leader(system_prompt="你是主管", model=ModelRef("qwen", "qwen-max"))
    node = make_leader_review_node(leader, fake_llm)

    state = {
        "task": "开发",
        "messages": [],
        "plan": [
            {"worker": "coder", "instruction": "写代码", "status": "running"},
            {"worker": "tester", "instruction": "写测试", "status": "pending"},
        ],
        "current_step": 0,
        "worker_outputs": {"coder": "print('hi')"},
        "audit_events": [],
    }
    result = node(state)

    assert result["plan"][0]["status"] == "done"
    assert result["plan"][1]["status"] == "pending"
    assert result["current_step"] == 1
    assert len(result["messages"]) == 1
    assert len(result["audit_events"]) == 1


def test_leader_plan_emits_trace_event(fake_llm, fake_trace_writer):
    """leader_plan 节点 emit leader_plan 轨迹事件。"""
    fake_llm.set_structured_responses(
        [Plan(steps=[PlanStep(worker="w1", instruction="do x")])]
    )
    leader = Leader(name="leader", system_prompt="test")
    node = make_leader_plan_node(leader, fake_llm, trace_writer=fake_trace_writer)
    state = {"task": "test task", "run_id": "run-1"}
    node(state)
    assert len(fake_trace_writer.events) == 1
    assert fake_trace_writer.events[0]["event_type"] == "leader_plan"
    assert fake_trace_writer.events[0]["actor"] == "leader"
    assert fake_trace_writer.events[0]["run_id"] == "run-1"


def test_worker_node_emits_start_and_end_events(fake_llm, fake_trace_writer):
    """worker 节点 emit worker_start 和 worker_end 轨迹事件。"""
    fake_llm.set_invoke_responses([AIMessage(content="done")])
    worker = Worker(name="w1", role="r", description="", system_prompt="test")
    node = make_worker_node(worker, fake_llm, [], trace_writer=fake_trace_writer)
    state = {
        "plan": [{"worker": "w1", "instruction": "do x", "status": "pending"}],
        "current_step": 0,
        "run_id": "run-1",
    }
    node(state)
    event_types = [e["event_type"] for e in fake_trace_writer.events]
    assert "worker_start" in event_types
    assert "worker_end" in event_types


def test_leader_review_emits_trace_event(fake_llm, fake_trace_writer):
    """leader_review 节点 emit leader_review 轨迹事件。"""
    fake_llm.set_invoke_responses([AIMessage(content="good job")])
    leader = Leader(name="leader", system_prompt="test")
    node = make_leader_review_node(leader, fake_llm, trace_writer=fake_trace_writer)
    state = {
        "plan": [{"worker": "w1", "instruction": "do x", "status": "done"}],
        "current_step": 0,
        "worker_outputs": {"w1": "done"},
        "run_id": "run-1",
    }
    node(state)
    assert len(fake_trace_writer.events) == 1
    assert fake_trace_writer.events[0]["event_type"] == "leader_review"


def test_node_without_trace_writer_works(fake_llm):
    """不传 trace_writer 时节点正常工作（向后兼容）。"""
    fake_llm.set_structured_responses(
        [Plan(steps=[PlanStep(worker="w1", instruction="do x")])]
    )
    leader = Leader(name="leader", system_prompt="test")
    node = make_leader_plan_node(leader, fake_llm)
    result = node({"task": "test", "run_id": "run-1"})
    assert "plan" in result
