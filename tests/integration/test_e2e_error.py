"""E2E: Worker 执行异常 → run 失败。

FakeLLM 不设 invoke_responses → invoke 时 IndexError → run 状态 failed。
"""
from agentteam.runtime.nodes import Plan, PlanStep
from agentteam.storage.audit import AuditRepo
from tests.conftest import FakeLLM
from tests.integration.conftest import make_dev_team_compiled, _wait_for_status


def test_e2e_worker_error_fails_run(run_manager, run_repo, integration_db):
    """Worker LLM 异常 → run 状态 failed。"""
    fake_llm = FakeLLM()
    # Leader plan 正常返回,但不设 invoke_responses
    # Worker invoke 时 IndexError → 异常 → run failed
    fake_llm.set_structured_responses([
        Plan(steps=[PlanStep(worker="analyst", instruction="分析需求")]),
    ])
    # 不设 invoke_responses → analyst worker invoke 时 IndexError

    graph = make_dev_team_compiled(fake_llm, integration_db)
    run_id = run_repo.create_run("dev_team_test", "开发功能")
    config = {"configurable": {"thread_id": run_id}}

    run_manager.start_run(run_id, graph, config, "开发功能")
    status = _wait_for_status(run_repo, run_id)
    assert status == "failed"

    # 验证 run 记录
    run = run_repo.get_run(run_id)
    assert run["status"] == "failed"
    assert run["ended_at"] is not None


def test_e2e_error_events_in_audit(run_manager, run_repo, integration_db):
    """run 失败后,audit 表有 error 事件。"""
    fake_llm = FakeLLM()
    fake_llm.set_structured_responses([
        Plan(steps=[PlanStep(worker="analyst", instruction="分析需求")]),
    ])

    graph = make_dev_team_compiled(fake_llm, integration_db)
    run_id = run_repo.create_run("dev_team_test", "开发功能")
    config = {"configurable": {"thread_id": run_id}}

    run_manager.start_run(run_id, graph, config, "开发功能")
    status = _wait_for_status(run_repo, run_id)
    assert status == "failed"

    # 验证 audit 事件
    audit_repo = AuditRepo(integration_db)
    events = audit_repo.list_events(run_id)
    event_types = [e["event_type"] for e in events]
    assert "run_start" in event_types
    assert "error" in event_types
