"""盲区#7：服务重启后 late client SSE replay 测试。

场景：服务重启（内存清空、SQLite 完整保留）后，迟到的 SSE 客户端连接
GET /api/runs/{run_id}/stream，应能从 SQLite 回放全部历史事件。

本文件通过"重建 app 指向同一 db_path、RunManager/EventBus 全新"模拟服务重启：
1. app1 创建 DB、注册 team、启动 run、等待终态
2. 关闭 app1 的 SQLite 连接（模拟 clean shutdown）
3. app2 用同一 db_path 重建（RunManager/EventBus/TeamStore 内存为空 —— 模拟
   重启后内存状态丢失，但 SQLite 数据完整保留）
4. 在 app2 上连 SSE，验证从 SQLite 回放的历史事件完整

注：盲区#1（MCP loader real E2E）已确认为接受限制——依赖 npx/真实 MCP server，
环境相关，难以在 CI 中稳定运行。BUG-06 的缓存幂等测试（tests/tools/test_mcp_leak.py）
已覆盖 MCP loader 等价的 idempotent 语义，本文件不再重复覆盖。
"""
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver

from agentteam.api.events import EventBus
from agentteam.api.routes.runs import runs_router
from agentteam.api.routes.teams import teams_router
from agentteam.api.run_manager import RunManager
from agentteam.api.store import TeamStore
from agentteam.storage.audit import AuditRepo
from agentteam.storage.db import init_db
from agentteam.storage.runs import RunRepo
from agentteam.tools.registry import ToolRegistry
from tests.api.conftest import _wait_for_run, make_provider_with_plan, make_team_json


def _build_app_with_db_path(db_path, provider=None):
    """手动创建 app 并暴露 conn，便于模拟服务重启。

    基于 tests/api/test_api_approvals_robustness.py 的 _build_app_with_run_manager 改造：
    - db_path 提为参数（原实现硬编码 tmp_path / "test.db"），便于 app2 复用同一文件
    - provider 可注入（中断场景需要带审批的 provider）
    - 返回 conn 引用，调用方可在重建 app2 前显式 close 模拟 clean shutdown

    每次 call 都创建全新的 RunManager/EventBus/TeamStore（内存状态为空），
    但 SQLite 数据由 db_path 文件保留——这正是"服务重启"的语义：
    in-memory state cleared, but SQLite intact。
    """
    conn = init_db(db_path)
    conn_lock = threading.Lock()
    run_repo = RunRepo(conn, lock=conn_lock)
    audit_repo = AuditRepo(conn, lock=conn_lock)
    team_store = TeamStore()
    event_bus = EventBus()
    run_manager = RunManager(run_repo, audit_repo, event_bus)
    provider = provider or make_provider_with_plan()
    tr = ToolRegistry()
    saver = SqliteSaver(conn)
    saver.lock = conn_lock
    saver.setup()

    app = FastAPI()
    app.include_router(teams_router(team_store))
    app.include_router(
        runs_router(
            run_manager, team_store, provider, tr, run_repo, audit_repo, event_bus,
            checkpointer=saver,
        )
    )
    return app, run_manager, run_repo, audit_repo, event_bus, conn


def test_sse_replay_after_app_recreation(tmp_path):
    """盲区#7：run 完成后服务重启，late client 连 SSE 应回放完整历史。

    场景：
    1. app1 注册 team + 启动 run + 等待 completed
    2. 用 GET /api/runs/{run_id}/trace 捕获 ground truth 事件类型
    3. 关闭 app1 的 SQLite 连接（模拟 clean shutdown）
    4. app2 指向同一 db_path 重建（RunManager/EventBus 内存为空，
       且不注册 team —— 模拟重启后内存状态丢失）
    5. 在 app2 上连 SSE，应回放 trace 中的全部事件类型，
       且因 run 已 completed，不应出现 run_interrupted 控制信号
    """
    db_path = str(tmp_path / "restart.db")

    # --- app1: 跑完一个 run ---
    app1, _, _, _, _, conn1 = _build_app_with_db_path(db_path)
    client1 = TestClient(app1)
    client1.post("/api/teams", json=make_team_json())
    resp = client1.post("/api/runs", json={"team_name": "dev", "task": "restart replay"})
    run_id = resp.json()["run_id"]
    status = _wait_for_run(client1, run_id)
    assert status == "completed", f"setup 失败：run 未完成（实际 {status}）"

    # trace 作为 ground truth：SSE 回放应产出与之等价的事件类型集合
    trace = client1.get(f"/api/runs/{run_id}/trace").json()
    expected_event_types = [e["event_type"] for e in trace]
    assert "run_start" in expected_event_types
    assert "run_end" in expected_event_types

    # 模拟 clean shutdown：关闭 app1 的 SQLite 连接
    conn1.close()

    # --- app2: 重建，指向同一 db_path，不注册 team（run_manager 无 graph/config）---
    # RunManager/EventBus 全新 —— 模拟重启后内存状态丢失，但 SQLite 完整保留
    app2, _, _, _, _, conn2 = _build_app_with_db_path(db_path)
    client2 = TestClient(app2)

    # late client 连 SSE：应从 SQLite 回放全部历史
    resp = client2.get(f"/api/runs/{run_id}/stream")
    assert resp.status_code == 200
    text = resp.text
    # 回放应包含 trace 中的全部事件类型
    for event_type in expected_event_types:
        assert event_type in text, f"回放缺少事件类型: {event_type}"
    # run 已 completed，不应出现 run_interrupted 控制信号
    assert "run_interrupted" not in text

    conn2.close()


def test_sse_replay_after_restart_interrupted_run(tmp_path):
    """盲区#7：中断的 run 服务重启后，late client 应收到补发的 run_interrupted 控制信号。

    run_interrupted 是纯控制信号（只推 EventBus 不写 SQLite）。
    若客户端在 run 中断后才连接，SSE 端点需从 run 状态推断并补发该信号，
    否则客户端不知道要弹审批框（见 runs.py lines 165-174）。

    服务重启后 EventBus 内存被清空（run_interrupted 信号已丢失），
    但 runs 表 status='interrupted' 仍在 SQLite 中。SSE 端点据此状态补发信号。

    场景：
    1. app1 注册带 step 审批的 team + 启动 run + 等待 interrupted
    2. 关闭 app1 的 SQLite 连接（EventBus 中的 run_interrupted 信号随之丢失）
    3. app2 重建（EventBus 内存清空）
    4. late client 连 SSE，应回放历史 + 补发 run_interrupted 控制信号
    """
    from agentteam.runtime.nodes import Plan, PlanStep
    from tests.conftest import FakeLLM, FakeModelProvider

    # 带 step 审批的 provider：跑完 plan 后即 interrupt，不调用 worker 的 invoke
    llm = FakeLLM()
    llm.set_structured_responses([Plan(steps=[PlanStep(worker="w1", instruction="do x")])])
    provider = FakeModelProvider({"qwen-max": llm})

    db_path = str(tmp_path / "restart_interrupted.db")

    # --- app1: 跑到 interrupted ---
    app1, _, _, _, _, conn1 = _build_app_with_db_path(db_path, provider=provider)
    client1 = TestClient(app1)
    client1.post("/api/teams", json=make_team_json(with_approval=True))
    resp = client1.post("/api/runs", json={"team_name": "dev", "task": "interrupted restart"})
    run_id = resp.json()["run_id"]
    status = _wait_for_run(client1, run_id)
    assert status == "interrupted", f"setup 失败：run 未到 interrupted（实际 {status}）"

    # 模拟 clean shutdown：EventBus 中的 run_interrupted 信号随之丢失
    conn1.close()

    # --- app2: 重建（EventBus 内存清空，run_interrupted 信号已丢失）---
    app2, _, _, _, _, conn2 = _build_app_with_db_path(db_path, provider=provider)
    client2 = TestClient(app2)

    # late client 连 SSE：应回放历史 + 补发 run_interrupted 控制信号
    resp = client2.get(f"/api/runs/{run_id}/stream")
    assert resp.status_code == 200
    text = resp.text
    assert "run_interrupted" in text, (
        "服务重启后 late client 应收到补发的 run_interrupted 控制信号"
    )

    conn2.close()


def test_sse_replay_empty_history_does_not_crash(tmp_path):
    """边界：run_id 在 runs 表存在但 audit_events 表无事件，SSE 不应抛异常。

    场景：通过 run_repo.create_run(...) 手动插入 run 行，不启动 graph，
    故 audit_events 表对该 run_id 无任何记录。

    SSE 端点先回放空历史（无事件 yield），再检查 run 状态：
    - 终态（completed/failed）→ 早退，响应正常结束（本测试覆盖此路径）
    - 中断态（interrupted）→ 补发 run_interrupted 后早退
    - 非终态（pending/running）→ 进入 live mode 无限等待

    为使测试可终止，这里把 run 置为终态 completed，验证"读完空历史后早退"
    路径不抛异常（status 200）。live mode 路径因无后台线程会无限阻塞，
    不在本测试覆盖范围——该路径由其他直播测试覆盖
    （如 test_api_runs.py 的 test_sse_connects_while_run_is_running）。
    """
    db_path = str(tmp_path / "empty_history.db")
    app, _, run_repo, _, _, conn = _build_app_with_db_path(db_path)
    client = TestClient(app)

    # 手动插入 run 行（不启动 graph，故无任何事件写入 audit_events）
    run_id = run_repo.create_run("dummy_team", "dummy task")
    # 置为终态，让 SSE 走"读完空历史后早退"路径，避免 live mode 无限等待
    run_repo.end_run(run_id, "completed")

    # 连 SSE —— 关键断言：不抛异常，返回 200
    resp = client.get(f"/api/runs/{run_id}/stream")
    assert resp.status_code == 200

    conn.close()
