"""审批 diff 预览: park gate 完整入参 + build_approval_preview。"""
import pytest

from agentteam.harness.approval import ApprovalBroker, ApprovalInterrupt
from agentteam.harness.approval_preview import build_approval_preview
from agentteam.harness.events import ObservedCall
from agentteam.runtime.trace import FakeTraceWriter
from agentteam.storage.audit import AuditRepo
from agentteam.storage.db import init_db


def _call(name="write_file", input_data=None) -> ObservedCall:
    return ObservedCall(
        call_id="c1", name=name,
        input=input_data if input_data is not None else
        {"path": "app.py", "content": "print('hi')"},
        status="completed", error=None,
    )


# ---------- park_tool_call: gate 携带完整入参 ----------

def test_park_gate_carries_full_input():
    broker = ApprovalBroker(FakeTraceWriter(), audit_repo=None)
    with pytest.raises(ApprovalInterrupt) as exc_info:
        broker.park_tool_call(
            "r1", "coder", _call(), "sess1", "requires_approval",
        )
    gate = exc_info.value.gate
    assert gate["input"] == {"path": "app.py", "content": "print('hi')"}
    assert gate["permission"] == "write_file"


def test_park_event_payload_excludes_full_input():
    """approval_requested 事件(SSE 回放/历史)不带完整 input,只带摘要。"""
    tw = FakeTraceWriter()
    broker = ApprovalBroker(tw, audit_repo=None)
    with pytest.raises(ApprovalInterrupt):
        broker.park_tool_call(
            "r1", "coder", _call(), "sess1", "requires_approval",
        )
    requested = [e for e in tw.events if e["event_type"] == "approval_requested"]
    assert len(requested) == 1
    assert "input" not in requested[0]["payload"]
    assert requested[0]["payload"]["args"].startswith("{")
    # tool_denied 仍记录摘要
    denied = [e for e in tw.events if e["event_type"] == "tool_denied"]
    assert denied and "input" not in denied[0]["payload"]


def test_park_gate_input_truncates_huge_values():
    broker = ApprovalBroker(None, audit_repo=None)
    huge = "x" * 100_000
    with pytest.raises(ApprovalInterrupt) as exc_info:
        broker.park_tool_call(
            "r1", "coder", _call(input_data={"content": huge}), "s", "requires_approval",
        )
    stored = exc_info.value.gate["input"]["content"]
    assert len(stored) < 100_000
    assert stored.endswith("]") and "truncated" in stored[-40:]


def test_resolve_records_tool_info(tmp_path):
    """决策落账时 approvals 行带 tool_name/args_preview(历史列表展示用)。"""
    from agentteam.storage.runs import RunRepo

    conn = init_db(str(tmp_path / "a.db"))
    run_id = RunRepo(conn).create_run("dev", "任务")
    repo = AuditRepo(conn)
    tw = FakeTraceWriter()
    broker = ApprovalBroker(tw, audit_repo=repo)
    gate = {"gate": "tool", "permission": "bash",
            "args": '{"command": "ls"}'}
    assert broker.resolve(run_id, gate, True) is True
    rows = repo.list_approvals(run_id)
    assert len(rows) == 1
    assert rows[0]["tool_name"] == "bash"
    assert rows[0]["args_preview"] == '{"command": "ls"}'
    conn.close()


# ---------- build_approval_preview ----------

def test_preview_none_for_step_gate():
    assert build_approval_preview(None) is None
    assert build_approval_preview({"gate": "step", "step": "s1"})["kind"] == "args"


def test_preview_bash_command():
    preview = build_approval_preview({
        "permission": "bash", "input": {"command": "rm -rf build/", "description": "清理"},
    })
    assert preview["kind"] == "command"
    assert preview["command"] == "rm -rf build/"


def test_preview_read_tools_show_path():
    preview = build_approval_preview({
        "permission": "read_file", "input": {"path": "src/main.py"},
    })
    assert preview["kind"] == "path"
    assert preview["path"] == "src/main.py"


def test_preview_write_new_file_falls_back_to_content(tmp_path, monkeypatch):
    """目标文件不存在(新文件)→ 无 diff,给新内容全文。"""
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    preview = build_approval_preview({
        "permission": "write_file",
        "input": {"path": "new.py", "content": "line1\nline2"},
    })
    assert preview["kind"] == "content"
    assert preview["content"] == "line1\nline2"
    assert preview["total_lines"] == 2


def test_preview_write_existing_file_gives_unified_diff(tmp_path, monkeypatch):
    """现文件可读 → unified diff,含 +/- 行。"""
    target = tmp_path / "app.py"
    target.write_text("old line\n", encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    preview = build_approval_preview({
        "permission": "write_file",
        "input": {"path": "app.py", "content": "new line\n"},
    })
    assert preview["kind"] == "diff"
    assert "-old line" in preview["diff"]
    assert "+new line" in preview["diff"]


def test_preview_write_outside_workspace_falls_back_to_content(
    tmp_path, monkeypatch,
):
    """workspace 外(或 docker 分离部署读不到)→ 退化为内容全文,不报错。"""
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    preview = build_approval_preview({
        "permission": "write_file",
        "input": {"path": "../../etc/passwd", "content": "x"},
    })
    assert preview["kind"] == "content"


def test_preview_unknown_tool_args_fallback():
    preview = build_approval_preview({
        "permission": "mystery", "input": {"foo": "bar"},
    })
    assert preview["kind"] == "args"
    assert preview["tool"] == "mystery"


def test_preview_content_truncation_flagged(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    content = "\n".join(f"line{i}" for i in range(1000))
    preview = build_approval_preview({
        "permission": "write_file",
        "input": {"path": "big.py", "content": content},
    })
    assert preview["kind"] == "content"
    assert preview["truncated"] is True
    assert len(preview["content"].splitlines()) == 400  # _CONTENT_MAX_LINES
