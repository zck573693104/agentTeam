"""多级层级 + Team 嵌套 + 专家库集成测试。"""
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from agentteam.domain.agent import Agent, TeamRef
from agentteam.domain.library import AgentLibrary
from agentteam.domain.team import Team
from agentteam.models.provider import ModelRef
from agentteam.runtime.graph import TeamCompiler
from agentteam.runtime.nodes import Plan, PlanStep
from agentteam.tools.registry import ToolRegistry
from tests.conftest import FakeLLM, FakeModelProvider


def _initial_state(task="t", run_id="r1"):
    return {
        "messages": [], "task": task, "plan": [], "current_step": 0,
        "worker_outputs": {}, "audit_events": [], "run_id": run_id,
        "pending_approval": None, "total_tokens": 0, "path": "team:t",
    }


def test_e2e_three_level_chain():
    """3 级 supervisor 链：CEO → CTO → eng，全部跑通。"""
    # CEO LLM：拆 1 步给 cto
    ceo_llm = FakeLLM()
    ceo_llm.set_structured_responses([Plan(steps=[PlanStep(worker="cto", instruction="做技术")])])
    ceo_llm.set_invoke_responses([AIMessage(content="cto 干得不错")])

    # CTO LLM：拆 1 步给 eng
    cto_llm = FakeLLM()
    cto_llm.set_structured_responses([Plan(steps=[PlanStep(worker="eng", instruction="写代码")])])
    cto_llm.set_invoke_responses([AIMessage(content="eng 干得不错")])

    # eng LLM：直接给答案
    eng_llm = FakeLLM()
    eng_llm.set_invoke_responses([AIMessage(content="print('hello')")])

    provider = FakeModelProvider({
        "ceo-model": ceo_llm, "cto-model": cto_llm, "eng-model": eng_llm,
    })
    compiler = TeamCompiler(provider, ToolRegistry())
    team = Team(
        name="t", description="3 level",
        root=Agent(
            name="ceo", role="supervisor", system_prompt="CEO",
            model=ModelRef("qwen", "ceo-model"),
            children=[Agent(
                name="cto", role="supervisor", system_prompt="CTO",
                model=ModelRef("qwen", "cto-model"),
                children=[Agent(
                    name="eng", role="worker", system_prompt="eng",
                    model=ModelRef("qwen", "eng-model"),
                )],
            )],
        ),
        default_model=ModelRef("qwen", "ceo-model"),
    )
    graph = compiler.compile(team, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "3level"}}
    graph.invoke(_initial_state(), config)

    state = graph.get_state(config)
    assert not state.next, "图应该已完成"
    # eng 的产出
    assert state.values["worker_outputs"].get("eng") == "print('hello')"
    # leader_plan 事件（CEO 和 CTO 各 1 次）
    leader_plans = [e for e in state.values["audit_events"]
                    if e["event_type"] == "leader_plan"]
    assert len(leader_plans) == 2  # I1 follow-up 验证点


def test_e2e_team_nesting():
    """Team 嵌套：父 Team 引用子 Team，子 Team 内部独立编排。"""
    parent_llm = FakeLLM()
    parent_llm.set_structured_responses([Plan(steps=[PlanStep(worker="qa", instruction="测试")])])
    parent_llm.set_invoke_responses([AIMessage(content="qa 完成")])

    sub_llm = FakeLLM()
    sub_llm.set_structured_responses([Plan(steps=[PlanStep(worker="tester", instruction="写测试")])])
    sub_llm.set_invoke_responses([AIMessage(content="tester 完成")])

    tester_llm = FakeLLM()
    tester_llm.set_invoke_responses([AIMessage(content="assert True")])

    provider = FakeModelProvider({
        "parent-model": parent_llm,
        "sub-model": sub_llm,
        "tester-model": tester_llm,
    })
    compiler = TeamCompiler(provider, ToolRegistry())

    sub_team = Team(
        name="sub", description="sub",
        root=Agent(
            name="sub_lead", role="supervisor", system_prompt="sub",
            model=ModelRef("qwen", "sub-model"),
            children=[Agent(
                name="tester", role="worker", system_prompt="tester",
                model=ModelRef("qwen", "tester-model"),
            )],
        ),
        default_model=ModelRef("qwen", "sub-model"),
    )
    compiler.register_team(sub_team)

    main_team = Team(
        name="main", description="main",
        root=Agent(
            name="lead", role="supervisor", system_prompt="main",
            model=ModelRef("qwen", "parent-model"),
            children=[TeamRef(name="sub", alias="qa")],
        ),
        default_model=ModelRef("qwen", "parent-model"),
    )
    graph = compiler.compile(main_team, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "nesting"}}
    graph.invoke(_initial_state(), config)

    state = graph.get_state(config)
    assert not state.next
    # tester 的产出（嵌套子图中产出冒泡到父图）
    assert state.values["worker_outputs"].get("tester") == "assert True"


def test_e2e_library_ref():
    """专家库引用：Agent(ref=...) 被派活，库中 system_prompt 生效。"""
    ceo_llm = FakeLLM()
    ceo_llm.set_structured_responses([Plan(steps=[PlanStep(worker="eng", instruction="写代码")])])
    ceo_llm.set_invoke_responses([AIMessage(content="ok")])

    eng_llm = FakeLLM()
    eng_llm.set_invoke_responses([AIMessage(content="code done")])

    lib = AgentLibrary()
    lib.register(Agent(
        name="code_engineer", role="worker",
        system_prompt="template prompt for code engineer",
        max_iterations=5,
    ))

    provider = FakeModelProvider({"ceo-model": ceo_llm, "eng-model": eng_llm})
    compiler = TeamCompiler(provider, ToolRegistry(), library=lib)
    team = Team(
        name="t", description="t",
        root=Agent(
            name="ceo", role="supervisor", system_prompt="CEO",
            model=ModelRef("qwen", "ceo-model"),
            children=[Agent(
                name="eng", role="worker",
                model=ModelRef("qwen", "eng-model"),
                ref="library:code_engineer",
            )],
        ),
        default_model=ModelRef("qwen", "ceo-model"),
    )
    graph = compiler.compile(team, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "lib"}}
    graph.invoke(_initial_state(), config)

    state = graph.get_state(config)
    assert not state.next
    assert state.values["worker_outputs"].get("eng") == "code done"


def test_e2e_mixed_all_features():
    """混合：3 级链 + Team 嵌套 + 专家库。"""
    ceo_llm = FakeLLM()
    ceo_llm.set_structured_responses([Plan(steps=[
        PlanStep(worker="cto", instruction="做技术"),
    ])])
    ceo_llm.set_invoke_responses([AIMessage(content="cto done")])

    cto_llm = FakeLLM()
    cto_llm.set_structured_responses([Plan(steps=[
        PlanStep(worker="eng", instruction="写代码"),
        PlanStep(worker="qa", instruction="测试"),
    ])])
    cto_llm.set_invoke_responses([
        AIMessage(content="eng done"),
        AIMessage(content="qa done"),
    ])

    eng_llm = FakeLLM()
    eng_llm.set_invoke_responses([AIMessage(content="code")])

    sub_llm = FakeLLM()
    sub_llm.set_structured_responses([Plan(steps=[
        PlanStep(worker="tester", instruction="写测试"),
    ])])
    sub_llm.set_invoke_responses([AIMessage(content="sub done")])

    tester_llm = FakeLLM()
    tester_llm.set_invoke_responses([AIMessage(content="assert True")])

    lib = AgentLibrary()
    lib.register(Agent(
        name="code_engineer", role="worker",
        system_prompt="eng template", max_iterations=5,
    ))

    provider = FakeModelProvider({
        "ceo-model": ceo_llm, "cto-model": cto_llm,
        "eng-model": eng_llm, "sub-model": sub_llm, "tester-model": tester_llm,
    })
    compiler = TeamCompiler(provider, ToolRegistry(), library=lib)

    sub_team = Team(
        name="sub", description="sub",
        root=Agent(
            name="sub_lead", role="supervisor", system_prompt="sub",
            model=ModelRef("qwen", "sub-model"),
            children=[Agent(
                name="tester", role="worker", system_prompt="tester",
                model=ModelRef("qwen", "tester-model"),
            )],
        ),
        default_model=ModelRef("qwen", "sub-model"),
    )
    compiler.register_team(sub_team)

    main_team = Team(
        name="main", description="all features",
        root=Agent(
            name="ceo", role="supervisor", system_prompt="CEO",
            model=ModelRef("qwen", "ceo-model"),
            children=[Agent(
                name="cto", role="supervisor", system_prompt="CTO",
                model=ModelRef("qwen", "cto-model"),
                children=[
                    Agent(
                        name="eng", role="worker",
                        model=ModelRef("qwen", "eng-model"),
                        ref="library:code_engineer",
                    ),
                    TeamRef(name="sub", alias="qa"),
                ],
            )],
        ),
        default_model=ModelRef("qwen", "ceo-model"),
    )
    graph = compiler.compile(main_team, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "mixed"}}
    graph.invoke(_initial_state(), config)

    state = graph.get_state(config)
    assert not state.next
    assert state.values["worker_outputs"].get("eng") == "code"
    assert state.values["worker_outputs"].get("tester") == "assert True"
