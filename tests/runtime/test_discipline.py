"""SP12a 通信纪律测试。

覆盖:
- extract_question:QUESTION/问题 标记解析(一次最多一个/无标记 None)
- WorkerOutput.question 序列化往返(含旧快照向后兼容)
- leader_review 遇 worker 提问 → worker_attention 轨迹事件(原样透传)
- leader_plan 注入分派纪律(langgraph 侧;harness 侧由 test_memory 的
  harness 注入用例与 translator 单测覆盖)
"""
from __future__ import annotations

from types import SimpleNamespace

from agentteam.runtime.discipline import (
    LEADER_DISCIPLINE,
    WORKER_DISCIPLINE,
    extract_question,
)
from agentteam.runtime.nodes import (
    Plan,
    PlanStep,
    ReviewVerdict,
    WorkerOutput,
    make_leader_plan_node,
    make_leader_review_node,
)
from tests.conftest import FakeLLM


# ================= extract_question =================


def test_extract_question_question_marker():
    assert extract_question("done\nQUESTION: 用哪个分支?") == "用哪个分支?"


def test_extract_question_fullwidth_marker():
    assert extract_question("ok\n问题：要不要加索引？") == "要不要加索引？"


def test_extract_question_none_when_absent():
    assert extract_question("全部完成,无遗留") is None
    assert extract_question("") is None
    assert extract_question(None) is None  # type: ignore[arg-type]


def test_extract_question_multiple_takes_first():
    text = "QUESTION: 第一问?\nQUESTION: 第二问?"
    assert extract_question(text) == "第一问?"


def test_extract_question_empty_marker_is_none():
    assert extract_question("QUESTION:") is None
    assert extract_question("   QUESTION:   ") is None


def test_extract_question_inline_marker_ignored():
    """标记必须是行首(允许缩进),行中提及不算提问。"""
    assert extract_question("关于 QUESTION: 协议的说明") is None


# ================= WorkerOutput.question =================


def test_worker_output_from_text_extracts_question():
    out = WorkerOutput.from_text("完成\nQUESTION: 需要生产配置吗?")
    assert out.question == "需要生产配置吗?"
    # artifact 保持完整原文(不破坏 contains 类验收断言)
    assert "QUESTION: 需要生产配置吗?" in out.artifact


def test_worker_output_from_text_no_question():
    out = WorkerOutput.from_text("全部完成")
    assert out.question is None


def test_worker_output_dict_round_trip():
    out = WorkerOutput(artifact="a", question="q?")
    d = out.to_dict()
    assert d["question"] == "q?"
    restored = WorkerOutput.from_dict(d)
    assert restored.question == "q?"


def test_worker_output_from_dict_legacy_snapshot():
    """旧快照无 question 键 → 从 artifact 文本提取(向后兼容)。"""
    restored = WorkerOutput.from_dict({"artifact": "x\nQUESTION: 旧快照提问?"})
    assert restored.question == "旧快照提问?"


def test_worker_output_from_dict_legacy_str():
    restored = WorkerOutput.from_dict("纯文本\nQUESTION: str 形态提问?")
    assert restored.question == "str 形态提问?"


# ================= leader 纪律注入 =================


def test_leader_plan_injects_discipline():
    llm = FakeLLM()
    llm.set_structured_responses([
        Plan(steps=[PlanStep(worker="w1", instruction="do x")]),
    ])

    class _SpyLLM:
        """包装 FakeLLM,捕获 with_structured_output 收到的 messages。"""

        def __init__(self, inner):
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

    spy = _SpyLLM(llm)
    node = make_leader_plan_node(
        SimpleNamespace(name="leader", system_prompt="s"), spy)
    node({"run_id": "r1", "task": "t"})
    contents = [m.content for m in spy.captured]
    assert LEADER_DISCIPLINE in contents


# ================= leader_review → worker_attention =================


class _TraceSpy:
    """记录 emit 调用的假 TraceWriter。"""

    def __init__(self):
        self.events: list[tuple] = []

    def emit(self, run_id, event_type, actor, payload=None, **kwargs):
        self.events.append((run_id, event_type, actor, payload, kwargs))


def test_leader_review_emits_worker_attention():
    """worker 带结构化提问时,leader_review 原样透传 worker_attention 事件。"""
    llm = FakeLLM()
    llm.set_structured_responses([ReviewVerdict(passed=True, reason="ok")])
    trace = _TraceSpy()
    agent = SimpleNamespace(name="leader", system_prompt="s")
    node = make_leader_review_node(agent, llm, trace_writer=trace)
    state = {
        "run_id": "r1",
        "execution_mode": "sequential",
        "current_step": 0,
        "plan": [{"worker": "w1", "instruction": "do x", "status": "pending"}],
        "worker_outputs": {
            "w1": WorkerOutput(
                artifact="方案 A/B 二选一", question="选方案 A 还是 B?",
            ).to_dict(),
        },
    }
    node(state)
    attentions = [e for e in trace.events if e[1] == "worker_attention"]
    assert len(attentions) == 1
    _, _, actor, payload, _ = attentions[0]
    assert actor == "leader"
    assert payload == {"worker": "w1", "question": "选方案 A 还是 B?"}


def test_leader_review_no_attention_without_question():
    llm = FakeLLM()
    llm.set_structured_responses([ReviewVerdict(passed=True, reason="ok")])
    trace = _TraceSpy()
    agent = SimpleNamespace(name="leader", system_prompt="s")
    node = make_leader_review_node(agent, llm, trace_writer=trace)
    state = {
        "run_id": "r1",
        "execution_mode": "sequential",
        "current_step": 0,
        "plan": [{"worker": "w1", "instruction": "do x", "status": "pending"}],
        "worker_outputs": {"w1": WorkerOutput(artifact="done").to_dict()},
    }
    node(state)
    assert not [e for e in trace.events if e[1] == "worker_attention"]


def test_worker_discipline_text_has_question_protocol():
    """纪律文本本身声明 QUESTION 协议(防止文本被误改丢失协议)。"""
    assert "QUESTION:" in WORKER_DISCIPLINE
    assert "最多提一个" in WORKER_DISCIPLINE
