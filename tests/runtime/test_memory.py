"""记忆层: MemoryEngine 蒸馏 + build_memory_context 注入格式 + 双引擎注入点。"""
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage

from agentteam.config import override_settings
from agentteam.domain.serializer import team_from_dict
from agentteam.harness.engine import HarnessRunner
from agentteam.models.provider import ModelRef
from agentteam.runtime.memory import (
    MemoryEngine,
    _parse_memory_response,
    build_memory_context,
)
from agentteam.runtime.discipline import LEADER_DISCIPLINE
from agentteam.runtime.nodes import Plan, PlanStep, make_leader_plan_node
from agentteam.storage.audit import AuditRepo
from agentteam.storage.db import init_db
from agentteam.storage.memory import MemoryRepo
from agentteam.storage.runs import RunRepo
from tests.conftest import FakeLLM, FakeModelProvider

MEMORY_JSON = (
    '```json\n{"summary": "完成 X", "key_points": ["先装依赖"], '
    '"files": ["f.py"]}\n```'
)


@pytest.fixture
def env(tmp_path):
    conn = init_db(str(tmp_path / "mem.db"))
    lock = threading.Lock()
    repos = SimpleNamespace(
        run=RunRepo(conn, lock=lock),
        audit=AuditRepo(conn, lock=lock),
        memory=MemoryRepo(conn, lock=lock),
        conn=conn,
    )
    yield repos
    conn.close()


def _make_engine(repos, llm: FakeLLM) -> MemoryEngine:
    return MemoryEngine(
        model_provider=FakeModelProvider({"qwen-max": llm}),
        memory_repo=repos.memory,
        run_repo=repos.run,
        audit_repo=repos.audit,
        default_model=ModelRef("qwen", "qwen-max"),
    )


def _seed_run(repos, team="dev", outcome="completed", with_task=True) -> str:
    """造一个终态 run + 最小 trace,返回 run_id。"""
    run_id = repos.run.create_run(team, "实现 X")
    repos.run.end_run(run_id, outcome)
    if with_task:
        repos.audit.add_event(run_id, "run_start", "system", {"task": "实现 X"})
    repos.audit.add_event(run_id, "run_end", "system")
    return run_id


# ---------- MemoryEngine.trigger ----------

def test_trigger_distills_memory(env):
    llm = FakeLLM()
    llm.set_invoke_responses([AIMessage(content=MEMORY_JSON)])
    engine = _make_engine(env, llm)
    run_id = _seed_run(env)
    engine.trigger(run_id)
    rows = env.memory.list_for_team("dev")
    assert len(rows) == 1
    assert rows[0]["summary"] == "完成 X"
    assert rows[0]["meta"]["key_points"] == ["先装依赖"]
    assert rows[0]["outcome"] == "completed"


def test_trigger_non_json_response_falls_back_to_text(env):
    llm = FakeLLM()
    llm.set_invoke_responses([AIMessage(content="这只是一个普通回答,不是 JSON")])
    engine = _make_engine(env, llm)
    run_id = _seed_run(env)
    engine.trigger(run_id)
    rows = env.memory.list_for_team("dev")
    assert len(rows) == 1
    assert "普通回答" in rows[0]["summary"]
    assert rows[0]["meta"] is None


def test_trigger_team_scoped(env):
    llm = FakeLLM()
    llm.set_invoke_responses([AIMessage(content=MEMORY_JSON)])
    engine = _make_engine(env, llm)
    run_id = _seed_run(env, team="support")
    engine.trigger(run_id)
    assert env.memory.list_for_team("dev") == []
    assert len(env.memory.list_for_team("support")) == 1


def test_trigger_disabled_by_settings(env):
    llm = FakeLLM()
    engine = _make_engine(env, llm)
    run_id = _seed_run(env)
    with override_settings(memory_enabled=False):
        engine.trigger(run_id)
    assert env.memory.list_for_team("dev") == []
    assert llm._inv_idx == 0  # 未调用 LLM


def test_trigger_unknown_run_silent(env):
    engine = _make_engine(env, FakeLLM())
    engine.trigger("nonexistent")  # 不抛


def test_trigger_llm_failure_silent(env):
    """LLM 异常响应 → 静默吞掉,不落记忆不抛出。"""
    llm = FakeLLM()
    llm.invoke_responses = [RuntimeError("boom")]  # response.content 访问将失败
    engine = _make_engine(env, llm)
    run_id = _seed_run(env)
    engine.trigger(run_id)  # 不抛
    assert env.memory.list_for_team("dev") == []


# ---------- _parse_memory_response ----------

def test_parse_memory_response_valid_json():
    summary, meta, tokens = _parse_memory_response(MEMORY_JSON)
    assert summary == "完成 X"
    assert meta == {"key_points": ["先装依赖"], "files": ["f.py"]}
    assert tokens == 0


def test_parse_memory_response_empty_returns_none():
    assert _parse_memory_response("```json\n{}\n```") is None


# ---------- build_memory_context ----------

def _memories(n):
    # list_for_team 契约:新→旧。索引 0 是最新记忆。
    return [
        {"created_at": f"2026-10-3{i}T10:00:00", "outcome": "completed",
         "summary": f"记忆 {i}"}
        for i in range(n)
    ]


def test_build_memory_context_empty():
    assert build_memory_context([], 600) == ""
    assert build_memory_context(_memories(3), 0) == ""  # 预算 0 禁用


def test_build_memory_context_budget_trim():
    memories = _memories(8)
    # 每条约 40 字符 ≈ 10 token;预算 20 token=80 字符 → 最多放 2 条
    ctx = build_memory_context(memories, 20)
    assert ctx.startswith("<memory")
    assert ctx.count("\n- ") <= 2
    # 至少保留最新一条(索引 0)
    assert "记忆 0" in ctx


def test_build_memory_context_wraps_tag():
    ctx = build_memory_context(_memories(2), 600)
    assert ctx.startswith('<memory source="团队历史记忆(最近 2 次运行)">')
    assert ctx.rstrip().endswith("</memory>")
    assert "记忆 1" in ctx and "记忆 0" in ctx


# ---------- leader_plan 注入(langgraph 引擎) ----------

class _SpyLLM:
    """包装 FakeLLM,捕获 with_structured_output 收到的 messages。"""

    def __init__(self, inner: FakeLLM):
        self.inner = inner
        self.captured = None

    def with_structured_output(self, schema, **kwargs):
        inner_wrapper = self.inner.with_structured_output(schema, **kwargs)
        spy = self

        class _W:
            def invoke(self, messages, **kw):
                spy.captured = messages
                return inner_wrapper.invoke(messages, **kw)

        return _W()


def test_leader_plan_injects_memory_between_system_and_task():
    llm = FakeLLM()
    llm.set_structured_responses([
        Plan(steps=[PlanStep(worker="w1", instruction="do x")]),
    ])
    spy = _SpyLLM(llm)
    agent = SimpleNamespace(name="leader", system_prompt="你是主管")
    node = make_leader_plan_node(agent, spy)
    node({
        "run_id": "r1", "task": "实现 X",
        "memory_context": "<memory>历史记忆</memory>",
    })
    # SP12a:消息顺序 system → memory(insert(1))→ 分派纪律 → 任务;
    # 纪律最贴近任务(spec §3.1),记忆层语义不变
    kinds = [type(m).__name__ for m in spy.captured]
    assert kinds == ["SystemMessage", "SystemMessage", "SystemMessage", "HumanMessage"]
    assert spy.captured[1].content == "<memory>历史记忆</memory>"
    assert spy.captured[2].content == LEADER_DISCIPLINE


def test_leader_plan_without_memory_has_no_extra_message():
    llm = FakeLLM()
    llm.set_structured_responses([
        Plan(steps=[PlanStep(worker="w1", instruction="do x")]),
    ])
    spy = _SpyLLM(llm)
    node = make_leader_plan_node(SimpleNamespace(name="leader", system_prompt="s"), spy)
    node({"run_id": "r1", "task": "t"})
    # SP12a 后无 memory 也是 3 条:system + 分派纪律 + task(纪律常驻注入)
    assert len(spy.captured) == 3
    assert spy.captured[1].content == LEADER_DISCIPLINE


# ---------- harness 引擎注入 ----------

def _harness_team():
    return team_from_dict({
        "name": "t", "description": "",
        "root": {
            "name": "leader", "role": "supervisor", "system_prompt": "s",
            "children": [
                {"name": "w1", "role": "worker", "system_prompt": "w", "tools": []},
            ],
        },
        "default_model": {"provider": "qwen", "name": "qwen-max"},
        "skills": [], "mcp_servers": [],
    })


def _bare_runner(team, task="任务") -> HarnessRunner:
    """绕过 __init__(免 opencode client),只装配 _initial_state/invoke 用到的属性。"""
    r = HarnessRunner.__new__(HarnessRunner)
    r._team = team
    r._library = None
    r._skills = None
    r._task = task
    r._memory_context = ""
    r._mapper = MagicMock()
    r._state = None
    r._persist = MagicMock()
    r._drive = MagicMock()
    r._store = None
    return r


def test_harness_invoke_captures_memory_context():
    r = _bare_runner(_harness_team())
    r.invoke({"memory_context": "<memory>MEM</memory>", "task": "任务"}, {})
    assert r._memory_context == "<memory>MEM</memory>"
    instruction = r._state["frames"][0]["instruction"]
    assert instruction.startswith("<memory>MEM</memory>")
    assert instruction.endswith("任务")


def test_harness_without_memory_keeps_plain_instruction():
    r = _bare_runner(_harness_team())
    r.invoke({"task": "任务"}, {})
    assert r._memory_context == ""
    assert r._state["frames"][0]["instruction"] == "任务"
