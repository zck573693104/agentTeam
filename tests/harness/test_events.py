"""EventMapper：opencode v2 `session.next.*` 事件 → 观测调用 / token / 错误旗标。

契约依据 docs/opencode-harness-design.md §8（v1.18.32 实测：载荷在 `data`，
工具名最先出现在 tool.input.started，完整入参在 tool.input.ended 的 text）。
"""
from __future__ import annotations

from itertools import count

from agentteam.harness.events import EventMapper, ObservedCall
from agentteam.runtime.trace import FakeTraceWriter

_EVT_SEQ = count(1)


class FakeClient:
    """只提供 subscribe/unsubscribe 的替身（EventMapper 不碰 HTTP）。"""

    def __init__(self) -> None:
        self.subscribers: list = []

    def subscribe(self, handler) -> None:
        self.subscribers.append(handler)

    def unsubscribe(self, handler) -> None:
        self.subscribers.remove(handler)


def make_mapper(session_id="ses_1", agent_name="w1"):
    trace = FakeTraceWriter()
    m = EventMapper("run_test", trace, FakeClient())
    if session_id:
        m.register_session(session_id, agent_name)
    return m, trace


def ev(etype, **data):
    # 真实事件每条都有唯一 id（去重键），这里用递增序号模拟
    return {"id": f"evt_{next(_EVT_SEQ)}", "type": etype, "data": data}


# ---------- 会话过滤 ----------


def test_ignores_unregistered_sessions():
    m, trace = make_mapper()
    m.on_event(ev("session.next.tool.called", sessionID="ses_other",
                  callID="c1", tool="bash", input={}))
    assert m.observed_calls("ses_other") == []
    assert m.observed_calls("ses_1") == []
    assert trace.events == []


def test_events_without_session_id_are_dropped():
    m, _ = make_mapper()
    m.on_event({"type": "session.next.tool.called", "data": {"callID": "c1"}})
    assert m.total_tokens() == 0


# ---------- 工具调用观测 ----------


def test_tool_input_phases_yield_name_and_args():
    m, trace = make_mapper()
    m.on_event(ev("session.next.tool.input.started",
                  sessionID="ses_1", callID="c1", name="write"))
    m.on_event(ev("session.next.tool.input.ended", sessionID="ses_1",
                  callID="c1", text='{"path":"a.txt","content":"hi"}'))
    calls = m.observed_calls("ses_1")
    assert len(calls) == 1
    assert calls[0].name == "write"
    assert calls[0].input == {"path": "a.txt", "content": "hi"}
    assert calls[0].status == "pending"
    # tool_call 轨迹每个 callID 只发一次
    assert [e["event_type"] for e in trace.events] == ["tool_call"]
    assert trace.events[0]["payload"]["tools"] == ["write"]
    assert trace.events[0]["actor"] == "w1"


def test_tool_called_fills_name_for_started_only():
    m, _ = make_mapper()
    m.on_event(ev("session.next.tool.input.started",
                  sessionID="ses_1", callID="c9", name=""))
    m.on_event(ev("session.next.tool.called", sessionID="ses_1",
                  callID="c9", tool="read", input={"path": "b"}))
    call = m.observed_calls("ses_1")[0]
    assert call.name == "read" and call.input == {"path": "b"}


def test_tool_success_and_failed_settle_status():
    m, _ = make_mapper()
    m.on_event(ev("session.next.tool.input.started",
                  sessionID="ses_1", callID="c1", name="bash"))
    m.on_event(ev("session.next.tool.failed", sessionID="ses_1", callID="c1",
                  error={"message": "Unable to execute command"}))
    call = m.observed_calls("ses_1")[0]
    assert call.status == "error"
    assert call.error == "Unable to execute command"

    m.on_event(ev("session.next.tool.input.started",
                  sessionID="ses_1", callID="c2", name="read"))
    m.on_event(ev("session.next.tool.success", sessionID="ses_1",
                  callID="c2", result="ok"))
    assert m.observed_calls("ses_1")[1].status == "success"


def test_non_json_args_fall_back_to_raw():
    m, _ = make_mapper()
    m.on_event(ev("session.next.tool.input.started",
                  sessionID="ses_1", callID="c1", name="grep"))
    m.on_event(ev("session.next.tool.input.ended", sessionID="ses_1",
                  callID="c1", text="not json"))
    assert m.observed_calls("ses_1")[0].input == {"args": "not json"}


def test_args_preview_json_and_truncation():
    call = ObservedCall(call_id="c", name="write", input={"b": 1, "a": "x"})
    assert call.args_preview() == '{"a": "x", "b": 1}'
    long = ObservedCall(call_id="c", name="write", input={"a": "y" * 50})
    p = long.args_preview(limit=20)
    assert len(p) == 21 and p.endswith("…")


# ---------- 消费队列（事后拦截的现场保留） ----------


def test_take_new_calls_drains_and_requeue_prepends():
    m, _ = make_mapper()
    for cid in ("c1", "c2"):
        m.on_event(ev("session.next.tool.input.started",
                      sessionID="ses_1", callID=cid, name="bash"))
    first = m.take_new_calls("ses_1")
    assert [c.call_id for c in first] == ["c1", "c2"]
    assert m.take_new_calls("ses_1") == []
    # park：未判定的退回队头，resume 后先处理它们
    m.requeue_calls("ses_1", first)
    assert [c.call_id for c in m.take_new_calls("ses_1")] == ["c1", "c2"]
    m.on_event(ev("session.next.tool.input.started",
                  sessionID="ses_1", callID="c3", name="read"))
    m.requeue_calls("ses_1", first)
    assert [c.call_id for c in m.take_new_calls("ses_1")] == ["c1", "c2", "c3"]
    assert m.requeue_calls("ses_1", []) is None


# ---------- token / 完成 / 错误 ----------


def test_step_ended_accounts_tokens_and_turn_finish():
    m, _ = make_mapper()
    m.on_event(ev("session.next.step.ended", sessionID="ses_1",
                  finish="tool-calls", cost=0.01,
                  tokens={"input": 30, "output": 10, "reasoning": 2}))
    assert m.session_tokens("ses_1") == 42
    assert m.turn_finished("ses_1") is False  # agent loop 继续
    m.on_event(ev("session.next.step.ended", sessionID="ses_1",
                  finish="stop", tokens={"input": 5, "output": 1}))
    assert m.session_tokens("ses_1") == 48
    assert m.turn_finished("ses_1") is True
    assert m.total_tokens() == 48


def test_session_error_recorded():
    m, _ = make_mapper()
    m.on_event(ev("session.error", sessionID="ses_1",
                  error={"name": "ProviderAuthError", "message": "no key"}))
    assert m.session_error("ses_1") == "no key"


def test_missing_error_message_falls_back_to_name():
    m, _ = make_mapper()
    m.on_event(ev("session.error", sessionID="ses_1",
                  error={"name": "UnknownError"}))
    assert m.session_error("ses_1") == "UnknownError"


def test_step_failed_records_session_error():
    """实测：上游限流报的是 session.next.step.failed，不是 session.error。

    漏接它的话引擎只会轮询到空回合，报出「Leader plan is not valid JSON:
    empty answer」这种误导性错误。
    """
    m, _ = make_mapper()
    m.on_event(ev("session.next.step.failed", sessionID="ses_1",
                  assistantMessageID="m1",
                  error={"type": "unknown",
                         "message": "Provider request failed with HTTP 429: "
                                    "FreeUsageLimitError"}))
    assert m.session_error("ses_1").startswith("Provider request failed")


# ---------- 载荷位置与重放 ----------


def test_v1_style_properties_payload_still_read():
    m, _ = make_mapper()
    m.on_event({"type": "session.next.step.ended",
                "properties": {"sessionID": "ses_1", "finish": "stop",
                               "tokens": {"input": 7}}})
    assert m.session_tokens("ses_1") == 7
    assert m.turn_finished("ses_1") is True


def test_ingest_replays_durable_history():
    m, trace = make_mapper()
    history = [
        {"durable": {"seq": 1}, "type": "session.next.tool.input.started",
         "data": {"sessionID": "ses_1", "callID": "c1", "name": "bash"}},
        {"durable": {"seq": 2}, "type": "session.next.step.ended",
         "data": {"sessionID": "ses_1", "finish": "stop",
                  "tokens": {"input": 3, "output": 4}}},
    ]
    m.ingest("ses_1", history)
    assert m.session_tokens("ses_1") == 7
    assert [c.name for c in m.observed_calls("ses_1")] == ["bash"]
    assert [e["event_type"] for e in trace.events] == ["tool_call"]
    m.ingest("ses_1", [])  # 空历史不炸


def test_durable_seq_replay_is_idempotent():
    """SSE 与 history 是同一条事件的两份副本：重放不得重复计 token/重复观测。"""
    m, trace = make_mapper()
    events = [
        {"durable": {"seq": 1}, "type": "session.next.tool.input.started",
         "data": {"sessionID": "ses_1", "callID": "c1", "name": "bash"}},
        {"durable": {"seq": 2}, "type": "session.next.step.ended",
         "data": {"sessionID": "ses_1", "finish": "stop",
                  "tokens": {"input": 9, "output": 1}}},
    ]
    m.ingest("ses_1", events)      # SSE 侧先到
    m.ingest("ses_1", events)      # 收尾 history 对账
    m.ingest("ses_1", events[:1])  # 局部重放
    assert m.session_tokens("ses_1") == 10
    assert len(m.observed_calls("ses_1")) == 1
    assert [e["event_type"] for e in trace.events] == ["tool_call"]


def test_out_of_order_events_are_not_swallowed_by_dedupe():
    """去重不能用 seq 高水位：并发投递会乱序，后到的低 seq 工具事件必须处理，
    否则工具调用整批消失（曾导致事后拦截静默失效）。"""
    m, _ = make_mapper()
    step_ended = {"durable": {"seq": 9}, "type": "session.next.step.ended",
                  "data": {"sessionID": "ses_1", "finish": "stop",
                           "tokens": {"input": 1}}}
    tool = {"durable": {"seq": 4}, "type": "session.next.tool.input.started",
            "data": {"sessionID": "ses_1", "callID": "c1", "name": "bash"}}
    m.on_event(step_ended)
    m.on_event(tool)
    assert [c.name for c in m.observed_calls("ses_1")] == ["bash"]
    assert m.session_tokens("ses_1") == 1


def test_streaming_events_without_seq_always_processed():
    """delta 类事件不带 durable.seq（实测）：不能被去重逻辑吞掉。"""
    m, _ = make_mapper()
    for _ in range(2):
        m.on_event({"type": "session.next.text.delta", "durable": None,
                    "data": {"sessionID": "ses_1", "text": "x"}})
    assert m.observed_calls("ses_1") == []


def test_malformed_payloads_are_ignored():
    m, _ = make_mapper()
    m.on_event({"type": "session.next.tool.called", "data": "not a dict"})
    m.on_event({"type": "session.next.tool.called"})
    m.on_event({})
    assert m.observed_calls("ses_1") == []


# ---------- 订阅生命周期 ----------


def test_start_stop_subscribe_once():
    trace = FakeTraceWriter()
    client = FakeClient()
    m = EventMapper("run_test", trace, client)
    m.start()
    m.start()  # 幂等
    assert len(client.subscribers) == 1
    m.stop()
    assert client.subscribers == []
    m.stop()  # 重复 stop 不炸
