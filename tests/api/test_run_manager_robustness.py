"""BUG-05: RunManager._handle_invoke_result 不处理 get_state 异常的回归测试。

原实现直接 `state = graph.get_state(config)`。若 get_state 因 checkpoint 损坏
或 config 失效抛异常，异常会传播到 _run_in_background 的 except Exception，
调用 _handle_error 标记 run 为 failed。但若 graph.invoke 正常返回（通常意味着
interrupted 或完成），误标 failed 会让用户无法 approve 续跑。

修复后：对 get_state 单独 try/except，失败时保守标记为 interrupted 等待人工介入。
"""
from agentteam.api.events import EventBus
from agentteam.api.run_manager import RunManager
from agentteam.storage.audit import AuditRepo
from agentteam.storage.db import init_db
from agentteam.storage.runs import RunRepo


class _FakeState:
    """模拟 langgraph 的 StateSnapshot 最小接口。"""

    def __init__(self, next=None, values=None):
        self.next = next if next is not None else []
        self.values = values if values is not None else {}


class _FakeGraph:
    """假 graph：invoke 正常返回，get_state 可控抛异常。

    模拟 graph.invoke 已正常返回但 get_state 失败的场景（如 checkpoint 损坏）。
    """

    def __init__(self, *, get_state_raises=False, state_next=None, state_values=None):
        self._get_state_raises = get_state_raises
        self._state_next = state_next if state_next is not None else []
        self._state_values = state_values if state_values is not None else {}
        self.invoke_called = False

    def invoke(self, payload, config):
        # graph.invoke 正常返回，不抛异常
        self.invoke_called = True
        return {}

    def get_state(self, config):
        if self._get_state_raises:
            raise RuntimeError("checkpoint corrupted: unable to deserialize state")
        return _FakeState(next=self._state_next, values=self._state_values)


def _setup(tmp_path):
    conn = init_db(tmp_path / "test.db")
    run_repo = RunRepo(conn)
    audit_repo = AuditRepo(conn)
    bus = EventBus()
    rm = RunManager(run_repo, audit_repo, bus)
    return conn, run_repo, audit_repo, bus, rm


def test_handle_invoke_result_get_state_failure_doesnt_mark_failed(tmp_path):
    """get_state 抛异常时不应误标 run 为 failed。

    场景：graph.invoke 已正常返回，但随后 get_state(config) 因 checkpoint
    损坏抛 RuntimeError。原实现将异常传播到 _handle_error 标记 failed；
    修复后应保守标记为 interrupted，保留 approve 续跑能力。
    """
    conn, run_repo, audit_repo, bus, rm = _setup(tmp_path)

    run_id = run_repo.create_run("t", "task")
    config = {"configurable": {"thread_id": run_id}}
    # 订阅 EventBus 以捕获 run_interrupted 事件
    q = bus.subscribe(run_id)

    fake_graph = _FakeGraph(get_state_raises=True)

    # 直接调用 _handle_invoke_result 模拟 graph.invoke 已正常返回后的处理
    rm._handle_invoke_result(run_id, fake_graph, config)

    run = run_repo.get_run(run_id)
    assert run["status"] != "failed", "get_state 失败时不应误标为 failed"
    assert run["status"] == "interrupted", (
        f"应保守标记为 interrupted 等待人工介入，实际: {run['status']}"
    )

    # 应推 run_interrupted 事件
    event = q.get(timeout=1.0)
    assert event["event_type"] == "run_interrupted"
    assert event["run_id"] == run_id

    conn.close()


def test_handle_invoke_result_state_next_marks_interrupted(tmp_path):
    """回归保障：get_state 正常且 state.next 非空时仍标记 interrupted。"""
    conn, run_repo, audit_repo, bus, rm = _setup(tmp_path)

    run_id = run_repo.create_run("t", "task")
    config = {"configurable": {"thread_id": run_id}}

    fake_graph = _FakeGraph(state_next=["step_gate"])

    rm._handle_invoke_result(run_id, fake_graph, config)

    run = run_repo.get_run(run_id)
    assert run["status"] == "interrupted"
    conn.close()


def test_handle_invoke_result_state_empty_marks_completed(tmp_path):
    """回归保障：get_state 正常且 state.next 为空时标记 completed。"""
    conn, run_repo, audit_repo, bus, rm = _setup(tmp_path)

    run_id = run_repo.create_run("t", "task")
    config = {"configurable": {"thread_id": run_id}}

    fake_graph = _FakeGraph(state_next=[], state_values={"total_tokens": 42})

    rm._handle_invoke_result(run_id, fake_graph, config)

    run = run_repo.get_run(run_id)
    assert run["status"] == "completed"
    assert run["total_tokens"] == 42
    conn.close()
