"""E2E: 研发小队审批场景。

场景 1: Leader step 级审批 → interrupt → resume approved → worker 执行 → 完成
场景 2: Leader step 级审批被拒绝 → run 完成(无 worker 执行)
场景 3: Coder tool 级审批(write_file) → interrupt → resume approved → write_file 执行 → 完成
"""
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from agentteam.domain.approval import ApprovalPolicy
from agentteam.domain.team import Leader, Team
from agentteam.domain.worker import Worker
from agentteam.models.provider import ModelRef
from agentteam.runtime.nodes import Plan, PlanStep
from agentteam.tools.registry import ToolRegistry
from agentteam.tools.skills import register_builtin_skills
from tests.conftest import FakeLLM
from tests.integration.conftest import make_dev_team_compiled, _wait_for_status, compile_team_with_registry


def test_e2e_step_approval_interrupt_resume(run_manager, run_repo, integration_db):
    """Leader step 级审批:plan 后 interrupt → resume approved → worker 执行 → 完成。"""
    fake_llm = FakeLLM()
    fake_llm.set_structured_responses([
        Plan(steps=[PlanStep(worker="analyst", instruction="分析需求")]),
    ])
    # worker 与 leader_review 都在 resume 之后才执行,共 2 次 invoke:
    # [0] analyst agent_step → 返回答案
    # [1] leader_review → 点评
    fake_llm.set_invoke_responses([
        AIMessage(content="需求分析完成"),
        AIMessage(content="analyst 完成得不错"),
    ])

    graph = make_dev_team_compiled(
        fake_llm, integration_db,
        leader_policy=ApprovalPolicy(level="step"),
    )
    run_id = run_repo.create_run("dev_team_test", "开发功能")
    config = {"configurable": {"thread_id": run_id}}

    # 第一次 invoke:应在 step_gate 处 interrupt
    run_manager.start_run(run_id, graph, config, "开发功能")
    status = _wait_for_status(run_repo, run_id)
    assert status == "interrupted"

    # resume:批准
    run_manager.resume_run(run_id, approved=True, reason="同意")
    status = _wait_for_status(run_repo, run_id)
    assert status == "completed"

    run = run_repo.get_run(run_id)
    assert run["status"] == "completed"
    assert run["ended_at"] is not None

    # 验证 worker 执行了
    state = graph.get_state(config)
    assert "analyst" in state.values.get("worker_outputs", {})


def test_e2e_step_approval_rejected_terminates(run_manager, run_repo, integration_db):
    """Leader step 级审批被拒绝 → run 完成(无 worker 执行)。"""
    fake_llm = FakeLLM()
    fake_llm.set_structured_responses([
        Plan(steps=[PlanStep(worker="analyst", instruction="分析需求")]),
    ])
    # 拒绝后不执行 worker,所以不需要 invoke_responses

    graph = make_dev_team_compiled(
        fake_llm, integration_db,
        leader_policy=ApprovalPolicy(level="step"),
    )
    run_id = run_repo.create_run("dev_team_test", "开发功能")
    config = {"configurable": {"thread_id": run_id}}

    run_manager.start_run(run_id, graph, config, "开发功能")
    status = _wait_for_status(run_repo, run_id)
    assert status == "interrupted"

    # resume:拒绝
    run_manager.resume_run(run_id, approved=False, reason="不通过")
    status = _wait_for_status(run_repo, run_id)
    assert status == "completed"

    run = run_repo.get_run(run_id)
    assert run["status"] == "completed"
    assert run["ended_at"] is not None

    # worker 不应执行
    state = graph.get_state(config)
    assert "analyst" not in state.values.get("worker_outputs", {})

    # 验证拒绝决策被记录
    assert state.values.get("pending_approval", {}).get("approved") is False


def test_e2e_tool_approval_write_file(run_manager, run_repo, integration_db, tmp_path):
    """Coder 调用 write_file → tool 级审批 interrupt → resume approved → write_file 执行 → 完成。"""
    target = tmp_path / "output.txt"

    # 创建一个测试用 write_file 工具(写入 tmp_path)
    def write_test_file(path: str, content: str) -> str:
        p = tmp_path / path
        p.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} chars to {path}"

    write_tool = StructuredTool.from_function(
        name="write_file", description="写文件", func=write_test_file
    )

    fake_llm = FakeLLM()
    fake_llm.set_structured_responses([
        Plan(steps=[PlanStep(worker="coder", instruction="写文件")]),
    ])
    fake_llm.set_invoke_responses([
        # coder 第 1 轮:调 write_file 工具
        AIMessage(
            content="",
            tool_calls=[{
                "name": "write_file",
                "args": {"path": "output.txt", "content": "hello"},
                "id": "tc1",
                "type": "tool_call",
            }],
        ),
        # coder 第 2 轮:给最终答案
        AIMessage(content="文件已写入"),
        # leader review
        AIMessage(content="coder 完成得不错"),
    ])

    # 构建带 tool 级审批的团队
    # 覆盖 write_file 为测试版本
    reg = ToolRegistry()
    register_builtin_skills(reg)
    # 先注销内置 write_file,注册测试版本
    reg.unregister("write_file")
    reg.register(write_tool)

    team = Team(
        name="dev_team_test",
        description="测试用研发小队",
        leader=Leader(name="tech_lead", system_prompt="你是技术主管"),
        workers=[
            Worker(
                name="coder", role="代码工程师", description="写代码",
                system_prompt="你是代码工程师",
                tools=["write_file"],
                approval_policy=ApprovalPolicy(level="tool", targets=["write_file"]),
                max_iterations=10,
            ),
        ],
        default_model=ModelRef("qwen", "qwen-max"),
        skills=["read_file", "write_file", "list_dir", "search_web"],
    )

    graph = compile_team_with_registry(team, fake_llm, integration_db, reg)

    run_id = run_repo.create_run("dev_team_test", "写文件任务")
    config = {"configurable": {"thread_id": run_id}}

    # 第一次 invoke:应在 tool 审批处 interrupt
    run_manager.start_run(run_id, graph, config, "写文件任务")
    status = _wait_for_status(run_repo, run_id)
    assert status == "interrupted"

    # resume:批准
    run_manager.resume_run(run_id, approved=True, reason="同意写文件")
    status = _wait_for_status(run_repo, run_id)
    assert status == "completed"

    # 验证文件已写入
    assert target.read_text(encoding="utf-8") == "hello"

    # 验证 worker 产出
    state = graph.get_state(config)
    assert state.values["worker_outputs"]["coder"] == "文件已写入"
