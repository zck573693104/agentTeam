"""SP7b: RunManager 触发 EvolutionEngine 测试。"""
import time
from unittest.mock import MagicMock

from agentteam.api.run_manager import RunCancelledError, RunManager


def _make_run_manager(evolution_engine=None):
    return RunManager(
        run_repo=MagicMock(),
        audit_repo=MagicMock(),
        event_bus=MagicMock(),
        evolution_engine=evolution_engine,
    )


def test_run_manager_accepts_evolution_engine_param():
    """RunManager.__init__ 接受 evolution_engine 参数(默认 None)。"""
    rm = _make_run_manager()
    assert rm._evolution is None


def test_handle_invoke_result_completed_triggers_evolution():
    """run completed → 异步触发 evolution.trigger。"""
    mock_evo = MagicMock()
    rm = _make_run_manager(evolution_engine=mock_evo)
    # mock graph.get_state 返回 completed 状态
    mock_graph = MagicMock()
    mock_state = MagicMock()
    mock_state.next = []  # 无 next → completed
    mock_state.values = {"total_tokens": 100}
    mock_graph.get_state.return_value = mock_state

    rm._handle_invoke_result("r1", mock_graph, {"configurable": {"thread_id": "r1"}})
    # 等待 daemon thread 执行
    time.sleep(0.1)
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
    time.sleep(0.1)
    mock_evo.trigger.assert_not_called()


def test_handle_error_failed_triggers_evolution():
    """run failed(普通异常)→ 触发 evolution。"""
    mock_evo = MagicMock()
    rm = _make_run_manager(evolution_engine=mock_evo)
    rm._handle_error("r1", RuntimeError("bug"))
    time.sleep(0.1)
    mock_evo.trigger.assert_called_once_with("r1")


def test_handle_error_cancelled_does_not_trigger_evolution():
    """run cancelled(RunCancelledError)→ 不触发 evolution(用户主动取消)。"""
    mock_evo = MagicMock()
    rm = _make_run_manager(evolution_engine=mock_evo)
    rm._handle_error("r1", RunCancelledError())
    time.sleep(0.1)
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
