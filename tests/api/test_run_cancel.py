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
