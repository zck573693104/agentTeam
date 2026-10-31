"""审批 diff 预览: approvals 表 tool_name/args_preview 列(migration v13)。"""
import threading

from agentteam.storage.audit import AuditRepo
from agentteam.storage.db import init_db
from agentteam.storage.runs import RunRepo


def _seed_conn(tmp_path):
    """init_db + 造一个 run 行(approvals.run_id 有外键约束)。返回 (conn, run_id)。"""
    conn = init_db(str(tmp_path / "a.db"))
    run_id = RunRepo(conn).create_run("dev", "任务")
    return conn, run_id


def test_add_approval_persists_tool_info(tmp_path):
    conn, run_id = _seed_conn(tmp_path)
    repo = AuditRepo(conn, lock=threading.Lock())
    approval_id = repo.add_approval(
        run_id, tool_name="write_file", args_preview='{"path": "a.py"}'
    )
    row = repo.get_approval(approval_id)
    assert row["tool_name"] == "write_file"
    assert row["args_preview"] == '{"path": "a.py"}'
    conn.close()


def test_add_approval_tool_info_optional(tmp_path):
    """step/worker 级 gate 无工具信息,两列应为 None(向后兼容)。"""
    conn, run_id = _seed_conn(tmp_path)
    repo = AuditRepo(conn, lock=threading.Lock())
    approval_id = repo.add_approval(run_id)
    row = repo.get_approval(approval_id)
    assert row["tool_name"] is None
    assert row["args_preview"] is None
    conn.close()
