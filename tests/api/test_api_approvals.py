from tests.api.conftest import (
    _wait_for_run,
    make_provider_with_plan,
    make_team_json,
)
from tests.conftest import FakeLLM, FakeModelProvider


def test_approve_resumes_interrupted_run(make_client):
    """中断的 run 被 approve 后应续跑至完成。"""
    provider = make_provider_with_plan()
    client = make_client(provider)
    client.post("/api/teams", json=make_team_json(with_approval=True))

    resp = client.post("/api/runs", json={"team_name": "dev", "task": "approval test"})
    run_id = resp.json()["run_id"]
    status = _wait_for_run(client, run_id)
    assert status == "interrupted"

    # approve
    resp = client.post(
        f"/api/runs/{run_id}/approve", json={"approved": True, "reason": "looks good"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    # 等待完成
    status = _wait_for_run(client, run_id)
    assert status == "completed"


def test_reject_terminates_run(make_client):
    """拒绝后 run 不再继续（状态为 interrupted，不再有 pending）。"""
    provider = make_provider_with_plan()
    client = make_client(provider)
    client.post("/api/teams", json=make_team_json(with_approval=True))

    resp = client.post("/api/runs", json={"team_name": "dev", "task": "reject test"})
    run_id = resp.json()["run_id"]
    _wait_for_run(client, run_id)

    resp = client.post(
        f"/api/runs/{run_id}/approve", json={"approved": False, "reason": "bad"}
    )
    assert resp.status_code == 200

    # 拒绝后 run 应最终结束（completed，因为拒绝后图走到 END）
    status = _wait_for_run(client, run_id)
    assert status == "completed"


def test_approve_non_interrupted_run_returns_400(make_client):
    """对非 interrupted 的 run approve 应返回 400。"""
    provider = make_provider_with_plan()
    client = make_client(provider)
    client.post("/api/teams", json=make_team_json())  # 无审批策略

    resp = client.post("/api/runs", json={"team_name": "dev", "task": "x"})
    run_id = resp.json()["run_id"]
    _wait_for_run(client, run_id)  # 等待完成

    resp = client.post(
        f"/api/runs/{run_id}/approve", json={"approved": True}
    )
    assert resp.status_code == 400


def test_approve_nonexistent_run_returns_404(make_client):
    client = make_client(FakeModelProvider({"qwen-max": FakeLLM()}))
    resp = client.post(
        "/api/runs/nonexistent/approve", json={"approved": True}
    )
    assert resp.status_code == 404


def test_double_approve_second_returns_400(make_client):
    """连续两次 approve 同一个 interrupted run：第一次成功（claim），第二次应 400。

    验证 try_claim 的原子条件更新防止并发双竞态。
    """
    provider = make_provider_with_plan()
    client = make_client(provider)
    client.post("/api/teams", json=make_team_json(with_approval=True))

    resp = client.post("/api/runs", json={"team_name": "dev", "task": "double approve"})
    run_id = resp.json()["run_id"]
    status = _wait_for_run(client, run_id)
    assert status == "interrupted"

    # 第一次 approve — try_claim 成功，状态 interrupted → running
    resp1 = client.post(
        f"/api/runs/{run_id}/approve", json={"approved": True, "reason": "first"}
    )
    assert resp1.status_code == 200

    # 第二次 approve — try_claim 失败（状态已非 interrupted），应 400
    resp2 = client.post(
        f"/api/runs/{run_id}/approve", json={"approved": True, "reason": "second"}
    )
    assert resp2.status_code == 400
    assert "not interrupted" in resp2.json()["detail"]
