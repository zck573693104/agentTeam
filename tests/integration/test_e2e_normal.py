"""E2E: 研发小队正常完成 run(无审批中断)。

用简化的 2-worker 团队(analyst + coder),mock LLM 按序返回:
Leader plan → analyst 执行 → Leader review → coder 执行 → Leader review → 结束
"""
from langchain_core.messages import AIMessage

from agentteam.runtime.nodes import Plan, PlanStep, ReviewVerdict
from tests.conftest import FakeLLM
from tests.integration.conftest import make_dev_team_compiled, _wait_for_status


def test_e2e_normal_completion(run_manager, run_repo, integration_db):
    """2-worker 研发小队正常完成 run,状态 pending→running→completed。"""
    fake_llm = FakeLLM()

    # leader_plan(Plan) 与 leader_review(ReviewVerdict) 都走 with_structured_output,
    # 共用 structured_responses 队列,按调用顺序排列: 先 Plan, 后 2 个 ReviewVerdict
    # (analyst / coder 完成后)。leader 与 worker 共用同一 fake_llm,
    # worker 的 agent_step 仍走 invoke(),留在 invoke_responses。
    fake_llm.set_structured_responses([
        Plan(steps=[
            PlanStep(worker="analyst", instruction="分析需求"),
            PlanStep(worker="coder", instruction="写代码"),
        ]),
        ReviewVerdict(passed=True, reason="analyst 干得不错"),
        ReviewVerdict(passed=True, reason="coder 代码到位,全部完成"),
    ])

    # invoke_responses 只剩 worker 的 agent_step 调用(leader_review 已改走 structured):
    # [0] analyst worker agent_step → 返回答案
    # [1] coder worker agent_step → 返回答案
    fake_llm.set_invoke_responses([
        AIMessage(content="需求分析完成:用户故事已拆解"),  # analyst
        AIMessage(content="print('hello world')"),         # coder
    ])

    graph = make_dev_team_compiled(fake_llm, integration_db)
    run_id = run_repo.create_run("dev_team_test", "开发 hello world 功能")
    config = {"configurable": {"thread_id": run_id}}

    run_manager.start_run(run_id, graph, config, "开发 hello world 功能")
    status = _wait_for_status(run_repo, run_id)
    assert status == "completed"

    # 验证 run 记录
    run = run_repo.get_run(run_id)
    assert run["status"] == "completed"
    assert run["ended_at"] is not None


def test_e2e_normal_worker_outputs(run_manager, run_repo, integration_db):
    """正常完成后,worker_outputs 包含两个 worker 的产出。"""
    fake_llm = FakeLLM()
    fake_llm.set_structured_responses([
        Plan(steps=[
            PlanStep(worker="analyst", instruction="分析需求"),
            PlanStep(worker="coder", instruction="写代码"),
        ]),
        ReviewVerdict(passed=True, reason="review 1"),
        ReviewVerdict(passed=True, reason="review 2"),
    ])
    # invoke_responses 只剩 worker 的 agent_step 调用(leader_review 已改走 structured)
    fake_llm.set_invoke_responses([
        AIMessage(content="需求分析结果"),    # analyst
        AIMessage(content="代码实现"),        # coder
    ])

    graph = make_dev_team_compiled(fake_llm, integration_db)
    run_id = run_repo.create_run("dev_team_test", "开发功能")
    config = {"configurable": {"thread_id": run_id}}

    run_manager.start_run(run_id, graph, config, "开发功能")
    _wait_for_status(run_repo, run_id)

    # 验证 graph 状态
    state = graph.get_state(config)
    values = state.values
    assert "analyst" in values.get("worker_outputs", {})
    assert "coder" in values.get("worker_outputs", {})
    assert values["worker_outputs"]["analyst"]["artifact"] == "需求分析结果"
    assert values["worker_outputs"]["coder"]["artifact"] == "代码实现"
