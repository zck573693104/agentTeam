"""RunManager：后台线程执行 LangGraph graph + interrupt/resume。"""
from __future__ import annotations

import threading
from typing import Any

from agentteam.api.events import EventBus
from agentteam.storage.audit import AuditRepo
from agentteam.storage.runs import RunRepo


class RunManager:
    """管理 run 的后台线程执行。

    - start_run: 在后台线程跑 graph.invoke()
    - resume_run: 用 Command(resume=...) 启新线程续跑
    - run 执行与 SSE 连接解耦
    """

    def __init__(self, run_repo: RunRepo, audit_repo: AuditRepo, event_bus: EventBus) -> None:
        self._run_repo = run_repo
        self._audit_repo = audit_repo
        self._bus = event_bus
        self._graphs: dict[str, Any] = {}
        self._configs: dict[str, dict] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def start_run(self, run_id: str, graph, config: dict, task: str) -> None:
        """在后台线程中跑 graph.invoke()，立即返回。"""
        with self._lock:
            self._graphs[run_id] = graph
            self._configs[run_id] = config
        self._run_repo.update_status(run_id, "running")
        thread = threading.Thread(
            target=self._run_in_background,
            args=(run_id, graph, config, task),
            daemon=True,
        )
        with self._lock:
            self._threads[run_id] = thread
        thread.start()

    def resume_run(self, run_id: str, approved: bool, reason: str | None = None) -> None:
        """用 Command(resume=...) 启新线程续跑。"""
        with self._lock:
            graph = self._graphs.get(run_id)
            config = self._configs.get(run_id)
        if graph is None or config is None:
            raise ValueError(f"Run {run_id} not found or not started")

        from langgraph.types import Command

        resume_value: dict[str, Any] = {"approved": approved, "decider": "api-user"}
        if reason:
            resume_value["reason"] = reason

        self._run_repo.update_status(run_id, "running")
        thread = threading.Thread(
            target=self._resume_in_background,
            args=(run_id, graph, config, Command(resume=resume_value)),
            daemon=True,
        )
        with self._lock:
            self._threads[run_id] = thread
        thread.start()

    def wait(self, run_id: str, timeout: float = 30.0) -> None:
        """等待 run 的后台线程结束（测试用）。"""
        with self._lock:
            thread = self._threads.get(run_id)
        if thread:
            thread.join(timeout=timeout)

    def _cleanup_run(self, run_id: str) -> None:
        """清理已完成/失败的 run 的内存状态。

        interrupted 的 run 不清理——graph/config/threads 仍需用于 resume。
        """
        with self._lock:
            self._graphs.pop(run_id, None)
            self._configs.pop(run_id, None)
            self._threads.pop(run_id, None)

    def _run_in_background(self, run_id: str, graph, config: dict, task: str) -> None:
        try:
            eid = self._audit_repo.add_event(run_id, "run_start", "system", {"task": task})
            self._bus.publish(
                run_id,
                {"id": eid, "event_type": "run_start", "run_id": run_id, "payload": {"task": task}},
            )
            initial = {
                "messages": [],
                "task": task,
                "plan": [],
                "current_step": 0,
                "worker_outputs": {},
                "audit_events": [],
                "run_id": run_id,
                "pending_approval": None,
            }
            graph.invoke(initial, config)
            self._handle_invoke_result(run_id, graph, config)
        except Exception as e:
            self._handle_error(run_id, e)

    def _resume_in_background(self, run_id: str, graph, config: dict, command) -> None:
        try:
            graph.invoke(command, config)
            self._handle_invoke_result(run_id, graph, config)
        except Exception as e:
            self._handle_error(run_id, e)

    def _handle_invoke_result(self, run_id: str, graph, config: dict) -> None:
        state = graph.get_state(config)
        if state.next:
            # interrupted：保留 graph/config/threads 供 resume 使用，不清理
            self._run_repo.update_status(run_id, "interrupted")
            self._bus.publish(run_id, {"event_type": "run_interrupted", "run_id": run_id})
        else:
            self._run_repo.end_run(run_id, "completed")
            eid = self._audit_repo.add_event(run_id, "run_end", "system")
            self._bus.publish(
                run_id, {"id": eid, "event_type": "run_end", "run_id": run_id}
            )
            self._cleanup_run(run_id)

    def _handle_error(self, run_id: str, error: Exception) -> None:
        self._run_repo.end_run(run_id, "failed")
        eid = self._audit_repo.add_event(
            run_id, "error", "system", {"error": str(error)}
        )
        self._bus.publish(
            run_id,
            {
                "id": eid,
                "event_type": "error",
                "run_id": run_id,
                "payload": {"error": str(error)},
            },
        )
        self._cleanup_run(run_id)
