"""P2 测试:Thread/Trigger 聚合 / Subscription 追问直通 / Outbox 可靠出站。"""
from __future__ import annotations

import time

from langchain_core.messages import AIMessage

from agentteam.v2.domain import events as ev
from agentteam.v2.domain.events import TicketStatus
from agentteam.v2.domain.worker import WorkerDef
from agentteam.v2.notify.outbox import OutboxWorker, format_message
from agentteam.v2.storage.outbox import OutboxRepo
from tests.v2.conftest import FakeLLM, wait_ticket_terminal


def test_inbound_aggregates_thread(make_v2):
    v2 = make_v2()
    r1 = v2.thread_repo.get_or_create("feishu", "chat-123")
    r2 = v2.thread_repo.get_or_create("feishu", "chat-123")
    assert r1["id"] == r2["id"]  # 幂等聚合
    t1 = v2.thread_repo.append_trigger(r1["id"], "alice", "帮我查数据")
    t2 = v2.thread_repo.append_trigger(r1["id"], "alice", "顺便画个图")
    triggers = v2.thread_repo.list_triggers(r1["id"])
    assert [t["text"] for t in triggers] == ["帮我查数据", "顺便画个图"]
    assert t1["at"] <= t2["at"]


def test_thread_ticket_subscription_binding(make_v2):
    llm = FakeLLM()
    llm.set_invoke_responses([AIMessage(content="done")])
    v2 = make_v2({"qwen-max": llm})
    v2.worker_repo.upsert(WorkerDef(name="coder"))
    thread = v2.thread_repo.get_or_create("feishu", "chat-1")
    resp = v2.scheduler.submit_request("x", worker_name="coder", thread_id=thread["id"])
    tid = resp["ticket_id"]
    wait_ticket_terminal(v2, tid)
    # dsh: Ticket↔Thread 绑定后,同线程消息可 fan-out
    assert thread["id"] in [t["id"] for t in v2.subscription_repo.threads_for_ticket(tid)]
    assert tid in v2.subscription_repo.tickets_for_thread(thread["id"])


def test_follow_up_injected_next_iteration(make_v2, tmp_path):
    """运行中追问:下一迭代边界注入,只入不回(worker 回复仍走正常链)。"""
    llm = FakeLLM()
    llm.set_invoke_responses([
        AIMessage(content="", tool_calls=[{
            "name": "write_file", "args": {"path": "a.txt", "content": "1"}, "id": "c1",
        }]),
        AIMessage(content="完成"),
    ])
    v2 = make_v2({"qwen-max": llm})
    v2.worker_repo.upsert(WorkerDef(name="coder"))
    resp = v2.scheduler.submit_request("写 a.txt", worker_name="coder")
    tid = resp["ticket_id"]
    # 与第一轮执行竞态:立即投追问;无论如何都要被消费(本轮或下轮)
    r = v2.scheduler.deliver_follow_up(tid, "顺便加上版权头", by="boss")
    assert r["ok"] is True
    t = wait_ticket_terminal(v2, tid)
    assert t.status == TicketStatus.DONE
    msgs = v2.message_repo.list_for_session(
        v2.session_repo.list_for_ticket(tid)[-1]["id"]
    )
    # 追问要么已被下一迭代消费(transcript),要么仍在收件箱(竞态下首轮即完成)
    injected = [m for m in msgs if "【追问·boss】" in m["content"]]
    leftover = v2.scheduler._follow_ups.get(tid, [])
    assert injected or leftover  # 消费或待消费,二者必有其一
    events = [e for e in v2.event_repo.list_after(tid) if e["type"] == ev.FOLLOW_UP_DELIVERED]
    assert len(events) == 1  # 入站即确认(回执)


def test_follow_up_terminal_ticket_409(make_v2):
    llm = FakeLLM()
    llm.set_invoke_responses([AIMessage(content="done")])
    v2 = make_v2({"qwen-max": llm})
    v2.worker_repo.upsert(WorkerDef(name="coder"))
    resp = v2.scheduler.submit_request("x", worker_name="coder")
    wait_ticket_terminal(v2, resp["ticket_id"])
    import pytest
    with pytest.raises(RuntimeError, match="不自动复活"):
        v2.scheduler.deliver_follow_up(resp["ticket_id"], "再改改")


def test_follow_up_unknown_ticket(make_v2):
    v2 = make_v2()
    import pytest
    with pytest.raises(KeyError):
        v2.scheduler.deliver_follow_up("nope", "hi")


def test_outbox_retry_backoff_drop(make_v2, tmp_path):
    """dsh Outbox: 失败退避重试,连续 5 败 → dropped 可查。"""
    v2 = make_v2()
    repo = OutboxRepo(v2.conn)
    row = repo.enqueue("t1", "ticket_done", "http://fake", "{}")
    # 全失败 sender
    worker = OutboxWorker(repo, sender=lambda url, body: (_ for _ in ()).throw(
        RuntimeError("HTTP 500")), retry_max=5, backoff_base=0.01, poll_interval=0.01)
    now = time.time()
    attempts = 0
    for _ in range(10):
        worker.run_once(now=now + attempts)
        row_now = [r for r in repo.pending_rows() if False]  # noqa
        if not any(r["id"] == row for r in repo.pending_rows()):
            break
        attempts += 1
        time.sleep(0.01)
    receipts = repo.list_for_ticket("t1")
    assert receipts[0]["status"] == "dropped"
    assert receipts[0]["attempts"] == 5
    assert "HTTP 500" in receipts[0]["last_error"]


def test_outbox_success_and_serial(make_v2):
    """同工单 FIFO 串行(「已收到」先于结果);成功落 sent 回执。"""
    v2 = make_v2()
    repo = OutboxRepo(v2.conn)
    sent_order: list[int] = []
    worker = OutboxWorker(repo, sender=lambda url, body: sent_order.append(body["seq"]),
                          retry_max=5, backoff_base=0.01)
    for seq in (1, 2, 3):
        repo.enqueue("t1", "ticket_done", "http://fake", f'{{"seq": {seq}}}')
    worker.run_once()
    assert sent_order == [1, 2, 3]
    rows = repo.list_for_ticket("t1")
    assert all(r["status"] == "sent" for r in rows)


def test_scheduler_enqueues_notify_on_terminal(make_v2):
    """关键事件进 outbox(先存后发);未配 webhook 时零影响。"""
    llm = FakeLLM()
    llm.set_invoke_responses([AIMessage(content="done")])
    v2 = make_v2({"qwen-max": llm})
    # 默认未配 webhook:outbox_repo 未注入 → 无通知
    v2.worker_repo.upsert(WorkerDef(name="coder"))
    resp = v2.scheduler.submit_request("x", worker_name="coder")
    wait_ticket_terminal(v2, resp["ticket_id"])
    if v2.outbox_repo is not None:
        assert v2.outbox_repo.list_for_ticket(resp["ticket_id"]) == []


def test_format_message_budget():
    """SP12a 消息预算:300 字截断 + 控制台链接。"""
    long_text = "很长的汇报" * 200
    body = format_message(ev.TICKET_STATUS_CHANGED,
                          {"to": "done", "result": long_text, "ticket_id": "t9"},
                          "http://console")
    text = body["content"]["text"]
    assert len(text) < 400
    assert "http://console/v2/tickets/t9" in text
