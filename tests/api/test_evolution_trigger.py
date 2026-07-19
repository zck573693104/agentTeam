"""SP7b: RunManager 触发 EvolutionEngine 测试。"""
import threading
from unittest.mock import MagicMock

from agentteam.api.run_manager import RunCancelledError, RunManager


def _make_run_manager(evolution_engine=None):
    return RunManager(
        run_repo=MagicMock(),
        audit_repo=MagicMock(),
        event_bus=MagicMock(),
        evolution_engine=evolution_engine,
    )


def _make_triggered_event(mock_evo):
    """构造 threading.Event,mock_evo.trigger 调用时 set。

    替代 time.sleep(0.1):Event.wait 立即在 trigger 被调用时返回,
    既快又可靠,避免 CI 慢机器 flaky。
    """
    triggered = threading.Event()

    def _set_event(*args, **kwargs):
        triggered.set()

    mock_evo.trigger.side_effect = _set_event
    return triggered


def test_run_manager_accepts_evolution_engine_param():
    """RunManager.__init__ 接受 evolution_engine 参数(默认 None)。"""
    rm = _make_run_manager()
    assert rm._evolution is None


def test_handle_invoke_result_completed_triggers_evolution():
    """run completed → 异步触发 evolution.trigger。"""
    mock_evo = MagicMock()
    triggered = _make_triggered_event(mock_evo)
    rm = _make_run_manager(evolution_engine=mock_evo)
    # mock graph.get_state 返回 completed 状态
    mock_graph = MagicMock()
    mock_state = MagicMock()
    mock_state.next = []  # 无 next → completed
    mock_state.values = {"total_tokens": 100}
    mock_graph.get_state.return_value = mock_state

    rm._handle_invoke_result("r1", mock_graph, {"configurable": {"thread_id": "r1"}})
    # Event.wait 同步等待 daemon thread 调用 trigger,timeout 兜底防死锁
    assert triggered.wait(timeout=2.0), "evolution.trigger was not called within 2s"
    mock_evo.trigger.assert_called_once_with("r1")


def test_handle_invoke_result_interrupted_does_not_trigger_evolution():
    """run interrupted → 不触发 evolution(等待用户 approve)。"""
    mock_evo = MagicMock()
    rm = _make_run_manager(evolution_engine=mock_evo)
    mock_graph = MagicMock()
    mock_state = MagicMock()
    mock_state.next = ["some_node"]  # 有 next → interrupted
    mock_graph.get_state.return_value = mock_state

    rm._handle_invoke_result("r1", mock_graph, {})
    # 同步路径:interrupted 分支不启动 daemon thread,直接断言
    mock_evo.trigger.assert_not_called()


def test_handle_error_failed_triggers_evolution():
    """run failed(普通异常)→ 触发 evolution。"""
    mock_evo = MagicMock()
    triggered = _make_triggered_event(mock_evo)
    rm = _make_run_manager(evolution_engine=mock_evo)
    rm._handle_error("r1", RuntimeError("bug"))
    assert triggered.wait(timeout=2.0), "evolution.trigger was not called within 2s"
    mock_evo.trigger.assert_called_once_with("r1")


def test_handle_error_cancelled_does_not_trigger_evolution():
    """run cancelled(RunCancelledError)→ 不触发 evolution(用户主动取消)。"""
    mock_evo = MagicMock()
    rm = _make_run_manager(evolution_engine=mock_evo)
    rm._handle_error("r1", RunCancelledError())
    # 同步路径:cancelled 分支不启动 daemon thread,直接断言
    mock_evo.trigger.assert_not_called()


def test_no_evolution_engine_does_not_raise():
    """evolution_engine=None 时,_handle_invoke_result/_handle_error 不抛异常。"""
    rm = _make_run_manager(evolution_engine=None)
    mock_graph = MagicMock()
    mock_state = MagicMock()
    mock_state.next = []
    mock_state.values = {"total_tokens": 0}
    mock_graph.get_state.return_value = mock_state
    # 应正常执行,不抛 AttributeError
    rm._handle_invoke_result("r1", mock_graph, {})
    rm._handle_error("r1", RuntimeError("x"))


def test_trigger_exception_does_not_affect_run_result():
    """evolution.trigger 抛异常时,RunManager 主流程不受影响,run 终态已正确标记。

    回归测试:daemon thread 异常隔离契约。_trigger_evolution_async 用
    _safe_trigger wrapper 吞没所有异常,防止 daemon thread 静默死亡时
    异常信息走 Python 默认 stderr 路径造成排障困难。
    """
    mock_evo = MagicMock()
    mock_evo.trigger.side_effect = RuntimeError("evolution boom")
    rm = _make_run_manager(evolution_engine=mock_evo)

    # 调用 _handle_error,验证不抛异常(daemon thread 异常被吞没)
    rm._handle_error("r1", RuntimeError("run bug"))
    # 等待 daemon thread 执行(会抛异常但被 _safe_trigger 吞没)
    import time
    time.sleep(0.2)
    # run 状态已正确标记为 failed(不受 evolution 异常影响)
    rm._run_repo.end_run.assert_called_with("r1", "failed")
    # trigger 确实被调用一次
    mock_evo.trigger.assert_called_once_with("r1")
