"""P0 全链路流测试:请求 → Router → Ticket → Worker Session → 终态(FakeLLM,无真实 Key)。"""
from __future__ import annotations

import time

from langchain_core.messages import AIMessage

from agentteam.v2.domain import events as ev
from agentteam.v2.domain.events import TicketStatus
from agentteam.v2.domain.worker import WorkerDef
from agentteam.v2.kernel.router import RouterDecision
from tests.v2.conftest import FakeLLM, wait_ticket_terminal


def _add_worker(v2, name="coder", **kw):
    v2.worker_repo.upsert(WorkerDef(name=name, **kw))
    return name


def test_explicit_worker_dispatch_done(make_v2):
    llm = FakeLLM()
    llm.set_invoke_responses([AIMessage(content="done")])
    v2 = make_v2({"qwen-max": llm})
    _add_worker(v2)
    resp = v2.scheduler.submit_request("实现一个函数", worker_name="coder")
    assert resp["action"] == "dispatch"
    t = wait_ticket_terminal(v2, resp["ticket_id"])
    assert t.status == TicketStatus.DONE
    assert t.result == "done"
    assert t.mandate.basis == "explicit"
    assert t.worker_name == "coder"


def test_router_llm_dispatch(make_v2):
    llm = FakeLLM()
    llm.set_structured_responses([
        RouterDecision(action="dispatch", worker="coder", goal="g", acceptance="a", note="n"),
    ])
    llm.set_invoke_responses([AIMessage(content="done")])
    v2 = make_v2({"qwen-max": llm})
    _add_worker(v2)
    resp = v2.scheduler.submit_request("写个排序")
    assert resp["action"] == "dispatch"
    t = wait_ticket_terminal(v2, resp["ticket_id"])
    assert t.result == "done"


def test_router_reply_no_ticket(make_v2):
    llm = FakeLLM()
    llm.set_structured_responses([
        RouterDecision(action="reply", reply="这是说明,不需要动工"),
    ])
    v2 = make_v2({"qwen-max": llm})
    _add_worker(v2)
    resp = v2.scheduler.submit_request("什么是 dsh 架构?")
    assert resp["action"] == "reply"
    assert resp["reply"] == "这是说明,不需要动工"
    assert v2.ticket_repo.list_by_status() == []


def test_router_fallback_unknown_worker(make_v2):
    llm = FakeLLM()
    llm.set_structured_responses([
        RouterDecision(action="dispatch", worker="ghost", goal="g"),
    ])
    llm.set_invoke_responses([AIMessage(content="done")])
    v2 = make_v2({"qwen-max": llm})
    _add_worker(v2, "coder")
    resp = v2.scheduler.submit_request("随便派")
    t = wait_ticket_terminal(v2, resp["ticket_id"])
    # 兜底派给第一个 enabled worker
    assert t.worker_name == "coder"
    assert t.status == TicketStatus.DONE


def test_no_worker_replies_error(make_v2):
    v2 = make_v2()
    resp = v2.scheduler.submit_request("没人")
    assert resp["action"] == "reply"
    assert "没有可用" in resp["reply"]


def test_worker_tool_write_then_finish(make_v2, tmp_path):
    llm = FakeLLM()
    llm.set_invoke_responses([
        AIMessage(content="", tool_calls=[{
            "name": "write_file",
            "args": {"path": "hello.txt", "content": "hi v2"},
            "id": "c1",
        }]),
        AIMessage(content="已写入 hello.txt"),
    ])
    v2 = make_v2({"qwen-max": llm})
    _add_worker(v2)
    resp = v2.scheduler.submit_request("写 hello.txt", worker_name="coder")
    t = wait_ticket_terminal(v2, resp["ticket_id"])
    assert t.status == TicketStatus.DONE
    # 文件落在该 ticket 的隔离工作区
    workspace = v2.session_repo.list_for_ticket(resp["ticket_id"])[0]["workspace_dir"]
    assert (tmp_path / "workspaces").exists()
    from pathlib import Path
    content = (Path(workspace) / "hello.txt").read_text(encoding="utf-8")
    assert content == "hi v2"
    # 事件流包含工具轨迹
    types = [e["type"] for e in v2.event_repo.list_after(resp["ticket_id"])]
    assert ev.TOOL_CALLED in types and ev.TOOL_RESULT in types


def test_unknown_tool_self_correct(make_v2):
    llm = FakeLLM()
    llm.set_invoke_responses([
        AIMessage(content="", tool_calls=[{"name": "nope", "args": {}, "id": "c1"}]),
        AIMessage(content="换了方法,done"),
    ])
    v2 = make_v2({"qwen-max": llm})
    _add_worker(v2)
    resp = v2.scheduler.submit_request("x", worker_name="coder")
    t = wait_ticket_terminal(v2, resp["ticket_id"])
    assert t.status == TicketStatus.DONE
    msgs = v2.message_repo.list_for_session(
        v2.session_repo.list_for_ticket(resp["ticket_id"])[-1]["id"]
    )
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    assert "未知工具" in tool_msgs[0]["content"]


def test_question_recorded_in_reply(make_v2):
    """P1 语义:终答带 QUESTION → 转 Attention 等人,不落终态。"""
    llm = FakeLLM()
    llm.set_invoke_responses([
        AIMessage(content="方案已就绪\nQUESTION: 部署到哪个环境?"),
    ])
    v2 = make_v2({"qwen-max": llm})
    _add_worker(v2)
    resp = v2.scheduler.submit_request("准备部署", worker_name="coder")
    tid = resp["ticket_id"]
    deadline = time.time() + 10
    while time.time() < deadline:
        t = v2.ticket_repo.get(tid)
        if t and t.status == TicketStatus.WAITING_HUMAN:
            break
        time.sleep(0.05)
    assert t.status == TicketStatus.WAITING_HUMAN
    pendings = [a for a in v2.attention_repo.list_by_status("pending") if a["ticket_id"] == tid]
    assert pendings and pendings[0]["payload"]["question"] == "部署到哪个环境?"


def test_worker_missing_ticket_failed(make_v2):
    v2 = make_v2()
    resp = v2.scheduler.submit_request("x", worker_name="ghost_worker")
    # Router 兜底不会选中不存在的 worker,这里直接造一个指向失效 worker 的 ticket
    from agentteam.v2.domain.mandate import Mandate
    from agentteam.v2.domain.ticket import Ticket
    t = Ticket(id="tdead", title="d", task="d", status=TicketStatus.PENDING,
               worker_name="ghost", mandate=Mandate(basis="explicit", policy_version="p"))
    v2.ticket_repo.create(t)
    v2.scheduler._queue.put("tdead")
    v2.scheduler._wake.set()
    import time
    deadline = time.time() + 5
    while time.time() < deadline:
        got = v2.ticket_repo.get("tdead")
        if got.status.is_terminal():
            break
        time.sleep(0.05)
    assert got.status == TicketStatus.FAILED
    assert "不存在" in got.result


def test_cancel_pending(make_v2):
    llm = FakeLLM()
    llm.set_invoke_responses([AIMessage(content="done")])
    v2 = make_v2({"qwen-max": llm})
    _add_worker(v2)
    resp = v2.scheduler.submit_request("x", worker_name="coder")
    # 与调度线程竞态:FakeLLM 即时完成,取消赶不上 → 直接验证终态
    t = wait_ticket_terminal(v2, resp["ticket_id"])
    assert t.status == TicketStatus.DONE
    # 手工造 pending ticket 验证取消语义
    from agentteam.v2.domain.mandate import Mandate
    from agentteam.v2.domain.ticket import Ticket
    t2 = Ticket(id="tp", title="p", task="p", status=TicketStatus.PENDING,
                worker_name="coder", mandate=Mandate(basis="explicit", policy_version="p"))
    v2.ticket_repo.create(t2)
    assert v2.scheduler.cancel("tp")
    assert v2.ticket_repo.get("tp").status == TicketStatus.CANCELLED
    assert v2.scheduler.cancel("tp") is False  # 终态不可再撤权


def test_tickets_dispatched_concurrently(make_v2):
    """回归:新工单经有界线程池并发执行(P1 曾退化为 dispatch 线程串行)。

    三个会话必须同时到达 LLM invoke 点(Barrier)才算通过;
    若退化为串行,barrier 超时 → 会话全部 failed。"""
    import threading

    class BarrierLLM(FakeLLM):
        def __init__(self, parties: int) -> None:
            super().__init__()
            self._barrier = threading.Barrier(parties, timeout=10)

        def invoke(self, messages, **kwargs):
            self._barrier.wait()
            return AIMessage(content="ok")

    v2 = make_v2({"qwen-max": BarrierLLM(3)})
    _add_worker(v2)
    tids = [v2.scheduler.submit_request("x", worker_name="coder")["ticket_id"]
            for _ in range(3)]
    for tid in tids:
        assert wait_ticket_terminal(v2, tid).status == TicketStatus.DONE
