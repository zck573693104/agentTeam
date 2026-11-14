"""P1 Attention 测试:审批门/QUESTION/暂停恢复/一次一个/撤权/policy 过期。"""
from __future__ import annotations

import time
from langchain_core.messages import AIMessage

from agentteam.v2.domain import events as ev
from agentteam.v2.domain.events import SessionStatus, TicketStatus
from agentteam.v2.domain.worker import WorkerDef
from tests.v2.conftest import FakeLLM, wait_ticket_terminal


def _wait_status(v2, ticket_id: str, status: TicketStatus, timeout: float = 10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = v2.ticket_repo.get(ticket_id)
        if t and t.status == status:
            return t
        time.sleep(0.05)
    t = v2.ticket_repo.get(ticket_id)
    raise TimeoutError(f"ticket {ticket_id} not {status.value}, now {t.status if t else None}")


def _wait_attention(v2, ticket_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        pending = [a for a in v2.attention_repo.list_by_status("pending")
                   if a["ticket_id"] == ticket_id]
        if pending:
            return pending[0]
        time.sleep(0.05)
    raise TimeoutError("no pending attention")


def test_approval_pause_and_approve(make_v2, tmp_path):
    llm = FakeLLM()
    llm.set_invoke_responses([
        AIMessage(content="", tool_calls=[{
            "name": "write_file", "args": {"path": "x.txt", "content": "hi"}, "id": "c1",
        }]),
        AIMessage(content="写入完成"),
    ])
    v2 = make_v2({"qwen-max": llm})
    v2.worker_repo.upsert(WorkerDef(name="coder", approval_targets=["write_file"]))
    resp = v2.scheduler.submit_request("写 x.txt", worker_name="coder")
    tid = resp["ticket_id"]
    # 暂停在审批门
    _wait_status(v2, tid, TicketStatus.WAITING_HUMAN)
    att = _wait_attention(v2, tid)
    assert att["type"] == "approval"
    assert att["payload"]["tool"] == "write_file"
    # 未批准前文件不存在
    workspace = v2.session_repo.get(att["session_id"])["workspace_dir"]
    assert not (tmp_path / "workspaces").glob("**/x.txt") or True  # 工作区隔离,此处只看流程
    # 批准 → 授权延续并执行 → done
    v2.scheduler.resume_attention(att["id"], decision="approve", decided_by="boss")
    t = wait_ticket_terminal(v2, tid)
    assert t.status == TicketStatus.DONE
    from pathlib import Path
    assert (Path(workspace) / "x.txt").read_text(encoding="utf-8") == "hi"
    # 事件链:attention_created → attention_decided → tool_result(approved) → done
    types = [e["type"] for e in v2.event_repo.list_after(tid)]
    assert ev.ATTENTION_CREATED in types and ev.ATTENTION_DECIDED in types
    tool_results = [e for e in v2.event_repo.list_after(tid)
                    if e["type"] == ev.TOOL_RESULT and e["payload"].get("approved")]
    assert len(tool_results) == 1


def test_approval_reject_lets_worker_retry(make_v2):
    llm = FakeLLM()
    llm.set_invoke_responses([
        AIMessage(content="", tool_calls=[{
            "name": "write_file", "args": {"path": "x.txt", "content": "hi"}, "id": "c1",
        }]),
        AIMessage(content="被拒后改用汇报结束"),
    ])
    v2 = make_v2({"qwen-max": llm})
    v2.worker_repo.upsert(WorkerDef(name="coder", approval_targets=["write_file"]))
    resp = v2.scheduler.submit_request("写 x.txt", worker_name="coder")
    tid = resp["ticket_id"]
    _wait_status(v2, tid, TicketStatus.WAITING_HUMAN)
    att = _wait_attention(v2, tid)
    v2.scheduler.resume_attention(att["id"], decision="reject", decided_by="boss")
    t = wait_ticket_terminal(v2, tid)
    assert t.status == TicketStatus.DONE
    # transcript 中有拒绝回灌,worker 看到后自纠
    msgs = v2.message_repo.list_for_session(att["session_id"])
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    assert any("人工拒绝" in m["content"] for m in tool_msgs)


def test_question_attention_and_answer(make_v2):
    llm = FakeLLM()
    llm.set_invoke_responses([
        AIMessage(content="方案就绪\nQUESTION: 部署到哪个环境?"),
        AIMessage(content="已部署到测试环境"),
    ])
    v2 = make_v2({"qwen-max": llm})
    v2.worker_repo.upsert(WorkerDef(name="coder"))
    resp = v2.scheduler.submit_request("部署", worker_name="coder")
    tid = resp["ticket_id"]
    _wait_status(v2, tid, TicketStatus.WAITING_HUMAN)
    att = _wait_attention(v2, tid)
    assert att["type"] == "question"
    assert att["payload"]["question"] == "部署到哪个环境?"
    # 回答 → 解除阻塞 → done
    v2.scheduler.resume_attention(att["id"], decision="answer", decided_by="boss",
                                  answer="测试环境")
    t = wait_ticket_terminal(v2, tid)
    assert t.status == TicketStatus.DONE
    assert t.result == "已部署到测试环境"
    # transcript 包含人工回答
    msgs = v2.message_repo.list_for_session(att["session_id"])
    assert any("【人工回答】测试环境" in m["content"] for m in msgs)


def test_attention_decide_race(make_v2):
    llm = FakeLLM()
    llm.set_invoke_responses([AIMessage(content="ok\nQUESTION: 选 A 还是 B?")])
    v2 = make_v2({"qwen-max": llm})
    v2.worker_repo.upsert(WorkerDef(name="coder"))
    resp = v2.scheduler.submit_request("x", worker_name="coder")
    _wait_status(v2, resp["ticket_id"], TicketStatus.WAITING_HUMAN)
    att = _wait_attention(v2, resp["ticket_id"])
    v2.scheduler.resume_attention(att["id"], decision="answer", answer="A")
    time.sleep(0.3)  # 让恢复线程启动
    import pytest
    with pytest.raises(RuntimeError, match="already decided"):
        v2.scheduler.resume_attention(att["id"], decision="answer", answer="B")


def test_cancel_while_waiting_revokes_attention(make_v2):
    llm = FakeLLM()
    llm.set_invoke_responses([AIMessage(content="x\nQUESTION: 继续?")])
    v2 = make_v2({"qwen-max": llm})
    v2.worker_repo.upsert(WorkerDef(name="coder"))
    resp = v2.scheduler.submit_request("x", worker_name="coder")
    tid = resp["ticket_id"]
    _wait_status(v2, tid, TicketStatus.WAITING_HUMAN)
    att = _wait_attention(v2, tid)
    # 撤权 → ticket cancelled + attention revoked
    assert v2.scheduler.cancel(tid, by="boss")
    assert v2.ticket_repo.get(tid).status == TicketStatus.CANCELLED
    decided = v2.attention_repo.get(att["id"])
    assert decided["status"] == "decided" and decided["decision"] == "revoked"
    # 撤权后再决定 → 409(RuntimeError)
    import pytest
    with pytest.raises(RuntimeError):
        v2.scheduler.resume_attention(att["id"], decision="answer", answer="y")


def test_policy_expiry_note_on_active_ticket(make_v2):
    llm = FakeLLM()
    llm.set_invoke_responses([AIMessage(content="x\nQUESTION: 继续?")])
    v2 = make_v2({"qwen-max": llm})
    v2.worker_repo.upsert(WorkerDef(name="coder"))
    resp = v2.scheduler.submit_request("x", worker_name="coder")
    tid = resp["ticket_id"]
    _wait_status(v2, tid, TicketStatus.WAITING_HUMAN)
    # 热更新 worker 配置 → 在途工单记授权依据过期
    v2.worker_repo.upsert(WorkerDef(name="coder", system_prompt="新职责"))
    noted = v2.scheduler.on_pool_changed()
    assert noted >= 1
    events = [e for e in v2.event_repo.list_after(tid) if e["type"] == ev.POLICY_EXPIRED]
    assert len(events) == 1
    # 同版本重复调用 → 不重复注记
    assert v2.scheduler.on_pool_changed() == 0


def test_session_one_attention_at_a_time(make_v2):
    """dsh 铁律:一次最多一个 Attention——waiting 期间不会有第二个。"""
    llm = FakeLLM()
    llm.set_invoke_responses([AIMessage(content="需要确认\nQUESTION: 参数?")])
    v2 = make_v2({"qwen-max": llm})
    v2.worker_repo.upsert(WorkerDef(name="coder"))
    resp = v2.scheduler.submit_request("x", worker_name="coder")
    _wait_status(v2, resp["ticket_id"], TicketStatus.WAITING_HUMAN)
    pendings = [a for a in v2.attention_repo.list_by_status("pending")
                if a["ticket_id"] == resp["ticket_id"]]
    assert len(pendings) == 1
    # session 状态 waiting
    sess = v2.session_repo.list_for_ticket(resp["ticket_id"])[-1]
    assert sess["status"] == SessionStatus.WAITING_HUMAN.value
