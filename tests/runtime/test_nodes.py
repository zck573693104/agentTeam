from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agentteam.domain.team import Leader
from agentteam.domain.worker import Worker
from agentteam.models.provider import ModelRef
from agentteam.runtime.nodes import (
    Plan,
    PlanStep,
    make_agent_step,
    make_finalize,
    make_init_worker,
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
    """max_iterations 到达时强制结束，LLM 被调用恰好 max_iterations 次。"""
    # 每次都返回 tool_calls，永不给出最终答案
    tool_call_response = AIMessage(
        content="",
        tool_calls=[{"name": "read_file", "args": {"path": "x"}, "id": "tc1", "type": "tool_call"}],
    )
    fake_llm.set_invoke_responses([tool_call_response] * 100)
    worker = Worker(
        name="w1", role="r", description="", system_prompt="test", max_iterations=3
    )
    node = make_worker_node(worker, fake_llm, [])
    state = {
        "plan": [{"worker": "w1", "instruction": "do x", "status": "pending"}],
        "current_step": 0,
        "run_id": "r1",
    }
    result = node(state)
    # LLM 应该被调用恰好 3 次（max_iterations=3）
    assert fake_llm._inv_idx == 3
    # 最终答案应该是非 None（强制结束时取最后一条消息）
    assert result["worker_outputs"]["w1"] is not None


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


def test_init_worker_sets_react_messages(fake_llm):
    """init_worker 从 plan/current_step 取 instruction，初始化 react_messages。"""
    worker = Worker(name="coder", role="r", description="", system_prompt="你是代码工程师")
    node = make_init_worker(worker)
    state = {
        "plan": [{"worker": "coder", "instruction": "写 hello", "status": "pending"}],
        "current_step": 0,
    }
    result = node(state)
    assert len(result["react_messages"]) == 2
    assert isinstance(result["react_messages"][0], SystemMessage)
    assert isinstance(result["react_messages"][1], HumanMessage)
    assert result["react_messages"][0].content == "你是代码工程师"
    assert result["react_messages"][1].content == "写 hello"


def test_init_worker_resets_iteration_and_tool_calls(fake_llm):
    """init_worker 初始化 iteration=0, tool_calls=[], final_answer=""。"""
    worker = Worker(name="w1", role="r", description="", system_prompt="test")
    node = make_init_worker(worker)
    state = {
        "plan": [{"worker": "w1", "instruction": "do x", "status": "pending"}],
        "current_step": 0,
    }
    result = node(state)
    assert result["iteration"] == 0
    assert result["tool_calls"] == []
    assert result["final_answer"] == ""


def test_init_worker_emits_worker_start_trace(fake_llm, fake_trace_writer):
    """init_worker emit worker_start 轨迹事件。"""
    worker = Worker(name="w1", role="r", description="", system_prompt="test")
    node = make_init_worker(worker, trace_writer=fake_trace_writer)
    state = {
        "plan": [{"worker": "w1", "instruction": "do x", "status": "pending"}],
        "current_step": 0,
        "run_id": "run-1",
    }
    node(state)
    assert len(fake_trace_writer.events) == 1
    assert fake_trace_writer.events[0]["event_type"] == "worker_start"
    assert fake_trace_writer.events[0]["actor"] == "w1"


def test_agent_step_with_tool_calls(fake_llm):
    """agent_step 有 tool_calls 时写入 tool_calls，追加 AIMessage 到 react_messages。"""
    fake_llm.set_invoke_responses([
        AIMessage(
            content="",
            tool_calls=[{"name": "read_file", "args": {"path": "x"}, "id": "tc1", "type": "tool_call"}],
        ),
    ])
    worker = Worker(name="w1", role="r", description="", system_prompt="test", max_iterations=5)
    from agentteam.tools.skills.file_ops import read_file
    node = make_agent_step(worker, fake_llm, [read_file])
    state = {
        "react_messages": [SystemMessage(content="test"), HumanMessage(content="do x")],
        "iteration": 0,
    }
    result = node(state)
    assert len(result["react_messages"]) == 1  # AIMessage appended
    assert result["tool_calls"] == [{"name": "read_file", "args": {"path": "x"}, "id": "tc1", "type": "tool_call"}]
    assert result["final_answer"] == ""


def test_agent_step_with_final_answer(fake_llm):
    """agent_step 无 tool_calls 时写入 final_answer。"""
    fake_llm.set_invoke_responses([AIMessage(content="任务完成")])
    worker = Worker(name="w1", role="r", description="", system_prompt="test")
    node = make_agent_step(worker, fake_llm, [])
    state = {
        "react_messages": [SystemMessage(content="test"), HumanMessage(content="do x")],
        "iteration": 0,
    }
    result = node(state)
    assert result["final_answer"] == "任务完成"
    assert result["tool_calls"] == []


def test_agent_step_appends_ai_message_to_react(fake_llm):
    """agent_step 始终追加 AIMessage 到 react_messages（不论有无 tool_calls）。"""
    fake_llm.set_invoke_responses([AIMessage(content="思考中...")])
    worker = Worker(name="w1", role="r", description="", system_prompt="test")
    node = make_agent_step(worker, fake_llm, [])
    state = {
        "react_messages": [SystemMessage(content="test")],
        "iteration": 0,
    }
    result = node(state)
    assert len(result["react_messages"]) == 1
    assert isinstance(result["react_messages"][0], AIMessage)


def test_finalize_writes_worker_output(fake_llm):
    """finalize 写 worker_outputs 和汇总 messages。"""
    worker = Worker(name="coder", role="r", description="", system_prompt="test")
    node = make_finalize(worker)
    state = {
        "final_answer": "print('hello')",
        "react_messages": [],
        "run_id": "run-1",
    }
    result = node(state)
    assert result["worker_outputs"] == {"coder": "print('hello')"}
    assert len(result["messages"]) == 1
    assert "coder" in result["messages"][0].content
    assert len(result["audit_events"]) == 1
    assert result["audit_events"][0]["event_type"] == "worker_end"


def test_finalize_fallback_to_last_ai_message(fake_llm):
    """final_answer 为空时（max_iterations 达上限），用最后一条 AIMessage 兜底。"""
    worker = Worker(name="w1", role="r", description="", system_prompt="test")
    node = make_finalize(worker)
    state = {
        "final_answer": "",
        "react_messages": [
            SystemMessage(content="test"),
            HumanMessage(content="do x"),
            AIMessage(content="还在思考..."),
        ],
        "run_id": "run-1",
    }
    result = node(state)
    assert result["worker_outputs"]["w1"] == "还在思考..."


def test_finalize_emits_worker_end_trace(fake_llm, fake_trace_writer):
    """finalize emit worker_end 轨迹事件。"""
    worker = Worker(name="w1", role="r", description="", system_prompt="test")
    node = make_finalize(worker, trace_writer=fake_trace_writer)
    state = {
        "final_answer": "done",
        "react_messages": [],
        "run_id": "run-1",
    }
    node(state)
    assert len(fake_trace_writer.events) == 1
    assert fake_trace_writer.events[0]["event_type"] == "worker_end"
    assert fake_trace_writer.events[0]["actor"] == "w1"
