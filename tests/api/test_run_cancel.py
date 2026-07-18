"""P4 Run 取消机制测试。

覆盖:
- RunCancelledError 异常类型(继承 BaseException)
- RunManager._cancel_events 基础设施(start_run 创建 event,is_cancelled 读 event)
- cancel_run(running / interrupted 两种路径)
- _handle_error 区分 RunCancelledError vs 普通异常
- make_agent_step 在 worker 入口检查取消信号
- POST /api/runs/{id}/cancel endpoint
"""
import threading
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from agentteam.api.events import EventBus
from agentteam.api.routes.runs import runs_router
from agentteam.api.routes.teams import teams_router
from agentteam.api.run_manager import RunCancelledError, RunManager
from agentteam.api.store import TeamStore
from agentteam.storage.audit import AuditRepo
from agentteam.storage.db import init_db
from agentteam.storage.runs import RunRepo
from agentteam.tools.registry import ToolRegistry
from tests.api.conftest import _wait_for_run, make_provider_with_plan, make_team_json


def _make_run_manager():
    """构造一个 RunManager,repo 全部 mock,便于断言调用。"""
    return RunManager(
        run_repo=MagicMock(),
        audit_repo=MagicMock(),
        event_bus=MagicMock(),
    )


def test_runcancellederror_inherits_baseexception():
    """RunCancelledError 继承 BaseException(非 Exception),避免被 worker 内 except Exception 吞没。"""
    assert issubclass(RunCancelledError, BaseException)
    assert not issubclass(RunCancelledError, Exception)


def test_is_cancelled_returns_false_before_cancel():
    """run 未被 cancel 时,is_cancelled 返回 False。"""
    rm = _make_run_manager()
    run_id = "run-1"
    # 模拟 start_run 已为该 run 创建 event(尚未 set)
    rm._cancel_events[run_id] = threading.Event()
    assert rm.is_cancelled(run_id) is False


def test_is_cancelled_returns_true_after_cancel_run():
    """cancel 信号发出(event 被 set)后,is_cancelled 返回 True。

    Task 1 阶段尚未实现 cancel_run(见 Task 2),
    此处直接 set event 模拟 cancel 信号已发出。
    """
    rm = _make_run_manager()
    run_id = "run-2"
    rm._cancel_events[run_id] = threading.Event()
    # 模拟 cancel_run 调用后会做的:set event
    rm._cancel_events[run_id].set()
    assert rm.is_cancelled(run_id) is True


def test_is_cancelled_returns_false_for_unknown_run():
    """未知 run_id(未 start_run)时 is_cancelled 返回 False,不抛 KeyError。"""
    rm = _make_run_manager()
    assert rm.is_cancelled("nonexistent-run") is False


def test_cancel_interrupted_run_ends_directly():
    """interrupted run cancel:直接 end_run('cancelled') + 发 run_cancelled 事件 + cleanup。

    简化方案(spec §6.3):interrupted 状态无需 recompile,直接结束。
    不应调用 update_status(interrupted 直接 end_run)。
    """
    rm = _make_run_manager()
    run_id = "run-interrupted"
    rm._run_repo.get_run.return_value = {"status": "interrupted"}

    result = rm.cancel_run(run_id)

    assert result is True
    # 直接 end_run("cancelled")
    rm._run_repo.end_run.assert_called_once_with(run_id, "cancelled")
    # 不走 update_status 路径(interrupted 直接结束)
    rm._run_repo.update_status.assert_not_called()
    # 发 run_cancelled 事件到 audit_repo
    rm._audit_repo.add_event.assert_called_once_with(run_id, "run_cancelled", "user")
    # publish 到 EventBus
    assert rm._bus.publish.called
    published = rm._bus.publish.call_args[0][1]
    assert published["event_type"] == "run_cancelled"
    assert published["run_id"] == run_id


def test_cancel_running_run_sets_event_and_status_cancelling():
    """running run cancel:set cancel event + update_status('cancelling')。

    running 状态不直接 end_run,而是设置 event 让 worker 检测后抛
    RunCancelledError,由 _handle_error 标 cancelled(Task 3)。
    """
    rm = _make_run_manager()
    run_id = "run-running"
    # 模拟 start_run 已创建 event
    rm._cancel_events[run_id] = threading.Event()
    rm._run_repo.get_run.return_value = {"status": "running"}

    result = rm.cancel_run(run_id)

    assert result is True
    # event 被 set
    assert rm._cancel_events[run_id].is_set() is True
    # 状态更新为 cancelling(中间态)
    rm._run_repo.update_status.assert_called_once_with(run_id, "cancelling")
    # running 状态不直接 end_run(等 worker 抛 RunCancelledError)
    rm._run_repo.end_run.assert_not_called()


def test_cancel_completed_run_returns_false():
    """completed run cancel:返回 False(不可取消)。"""
    rm = _make_run_manager()
    rm._run_repo.get_run.return_value = {"status": "completed"}

    result = rm.cancel_run("run-completed")

    assert result is False
    rm._run_repo.end_run.assert_not_called()
    rm._run_repo.update_status.assert_not_called()


def test_cancel_failed_run_returns_false():
    """failed run cancel:返回 False(不可取消)。"""
    rm = _make_run_manager()
    rm._run_repo.get_run.return_value = {"status": "failed"}

    result = rm.cancel_run("run-failed")

    assert result is False


def test_cancel_unknown_run_returns_false():
    """未知 run(get_run 返回 None)cancel:返回 False,不抛异常。"""
    rm = _make_run_manager()
    rm._run_repo.get_run.return_value = None

    result = rm.cancel_run("run-nonexistent")

    assert result is False


def test_handle_error_with_cancelled_error_marks_cancelled():
    """_handle_error 收到 RunCancelledError 时:标 cancelled + 发 run_cancelled 事件。

    场景:worker agent_step 检测到 cancel event 后抛 RunCancelledError,
    信号沿调用栈传播到 _run_in_background 的 except,由 _handle_error 收尾。
    """
    rm = _make_run_manager()
    run_id = "run-cancelled-by-worker"
    # 模拟 start_run 已创建 event(否则 _cleanup_run pop 时 KeyError,虽有 .pop(None) 兜底但仍需设置)
    rm._cancel_events[run_id] = threading.Event()

    rm._handle_error(run_id, RunCancelledError("Run run-cancelled-by-worker cancelled by user"))

    # 标 cancelled(不是 failed)
    rm._run_repo.end_run.assert_called_once_with(run_id, "cancelled")
    # 发 run_cancelled 事件(actor=user,表示用户触发)
    rm._audit_repo.add_event.assert_called_once_with(run_id, "run_cancelled", "user")
    # publish 到 EventBus
    assert rm._bus.publish.called
    published = rm._bus.publish.call_args[0][1]
    assert published["event_type"] == "run_cancelled"
    assert published["run_id"] == run_id
    # cleanup 被调用(event 已从 _cancel_events 移除)
    assert run_id not in rm._cancel_events


def test_handle_error_with_other_error_marks_failed():
    """回归保障:普通异常仍标 failed + 发 error 事件(不被 RunCancelledError 逻辑误触)。"""
    rm = _make_run_manager()
    run_id = "run-failed-by-bug"
    rm._cancel_events[run_id] = threading.Event()

    rm._handle_error(run_id, ValueError("something broke"))

    # 标 failed(不是 cancelled)
    rm._run_repo.end_run.assert_called_once_with(run_id, "failed")
    # 发 error 事件(actor=system)
    rm._audit_repo.add_event.assert_called_once_with(
        run_id, "error", "system", {"error": "something broke"}
    )
    # publish error 事件
    assert rm._bus.publish.called
    published = rm._bus.publish.call_args[0][1]
    assert published["event_type"] == "error"
    assert published["run_id"] == run_id
    # cleanup 被调用
    assert run_id not in rm._cancel_events


def test_run_in_background_catches_runcancellederror_via_baseexception():
    """_run_in_background 用 except BaseException 捕获 RunCancelledError。

    场景:graph.invoke 抛 RunCancelledError(BaseException)。
    若用 except Exception 会漏捕,run 卡 cancelling;
    改为 except BaseException 后能交给 _handle_error 标 cancelled。
    """
    rm = _make_run_manager()
    run_id = "run-bg-cancelled"
    rm._cancel_events[run_id] = threading.Event()

    # Fake graph:invoke 抛 RunCancelledError(模拟 worker 检测到 cancel)
    fake_graph = MagicMock()
    fake_graph.invoke.side_effect = RunCancelledError("cancelled")
    fake_graph.get_state.side_effect = RuntimeError("不应到达此处")

    # 直接调用 _run_in_background(不走 start_run 的线程,简化测试)
    rm._run_in_background(run_id, fake_graph, {}, "task")

    # 应标 cancelled(不是 failed,也不是卡 cancelling)
    rm._run_repo.end_run.assert_called_once_with(run_id, "cancelled")
    # add_event 被调用 2 次(run_start + run_cancelled),检查最后一次是 run_cancelled
    rm._audit_repo.add_event.assert_called_with(run_id, "run_cancelled", "user")
    # cleanup 被调用
    assert run_id not in rm._cancel_events
