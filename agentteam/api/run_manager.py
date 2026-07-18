"""RunManager：后台线程执行 LangGraph graph + interrupt/resume。"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Callable

from agentteam.api.events import BroadcastTraceWriter, EventBus
from agentteam.storage.audit import AuditRepo
from agentteam.storage.runs import RunRepo

if TYPE_CHECKING:
    from agentteam.domain.team import Team
    from agentteam.runtime.graph import TeamCompiler


class RunCancelledError(BaseException):
    """run 被用户取消,worker 节点检测到 cancel event 后抛出。

    继承 BaseException(而非 Exception)以绕过 worker 内部
    `try: ... except Exception:` 的常规 catch,确保取消信号能
    一路传播到 RunManager._handle_error 被识别并标记为 cancelled。
    """

    pass


class RunManager:
    """管理 run 的后台线程执行。

    - start_run: 在后台线程跑 graph.invoke()
    - resume_run: 用 Command(resume=...) 启新线程续跑
    - run 执行与 SSE 连接解耦
    """

    def __init__(
        self,
        run_repo: RunRepo,
        audit_repo: AuditRepo,
        event_bus: EventBus,
        checkpointer=None,
    ) -> None:
        self._run_repo = run_repo
        self._audit_repo = audit_repo
        self._bus = event_bus
        self._saver = checkpointer
        self._graphs: dict[str, Any] = {}
        self._configs: dict[str, dict] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._cancel_events: dict[str, threading.Event] = {}

    def has_graph(self, run_id: str) -> bool:
        """返回 run_id 是否有内存态 graph。

        供 approve_run 判断走 fast path(resume_run)还是 lazy recompile 路径。
        服务重启后 _graphs 清空,has_graph 返回 False → 触发 recompile。
        """
        with self._lock:
            return run_id in self._graphs

    def recompile_and_resume(
        self,
        run_id: str,
        team: "Team",
        compiler_factory: "Callable[[], TeamCompiler]",
        approved: bool,
        reason: str | None = None,
    ) -> None:
        """lazy recompile: 用 compiler_factory 构造 graph,注入内存,再 resume。

        供 approve_run 在 _graphs 缺失时(如服务重启后)调用。
        SqliteSaver 的 checkpoint 已持久化 interrupt 状态,
        graph.invoke(Command(resume=...)) 能从 checkpoint 续跑。

        参数:
            run_id: run 标识(同时作为 thread_id)
            team: 要重新编译的 Team(从 team_store.get(run["team_name"]) 取得)
            compiler_factory: 无参闭包,返回注册好所有 team 的 TeamCompiler。
                              抽成闭包避免 RunManager 直接依赖 ModelProvider/ToolRegistry 等。
            approved / reason: 透传给 resume_run

        异常契约:
            compiler_factory() / compiler.compile() / resume_run() 抛出的异常
            向上传播给调用方(approve_run),由 approve_run 的 try/except 兜底
            回滚 run 状态为 failed 并写 error audit event。本方法不做内部捕获,
            保持与 start_run / resume_run 一致的"异常由调用方处理"风格。
        """
        compiler = compiler_factory()
        trace_writer = BroadcastTraceWriter(self._audit_repo, self._bus)
        graph = compiler.compile(
            team,
            checkpointer=self._saver,
            trace_writer=trace_writer,
            audit_repo=self._audit_repo,
        )
        config = {"configurable": {"thread_id": run_id}}
        with self._lock:
            self._graphs[run_id] = graph
            self._configs[run_id] = config
        self.resume_run(run_id, approved, reason)

    def start_run(self, run_id: str, graph, config: dict, task: str) -> None:
        """在后台线程中跑 graph.invoke()，立即返回。"""
        with self._lock:
            self._graphs[run_id] = graph
            self._configs[run_id] = config
            self._cancel_events[run_id] = threading.Event()
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

    def is_cancelled(self, run_id: str) -> bool:
        """供 worker 节点轮询检查:run 是否被用户请求取消。

        未知 run_id(未 start_run 或已 cleanup)返回 False,不抛异常。
        """
        with self._lock:
            event = self._cancel_events.get(run_id)
        return event is not None and event.is_set()

    def cancel_run(self, run_id: str) -> bool:
        """请求取消 run。返回是否成功发出取消信号。

        两种状态分别处理(spec §6.3 简化方案):
        - interrupted: 直接 end_run("cancelled") + 发 run_cancelled 事件 + cleanup。
          无需 lazy recompile,因为 run 已暂停,直接结束即可。
        - running: set cancel event + update_status("cancelling")。
          worker 在下一次 agent_step 入口检测到 event 后抛 RunCancelledError,
          由 _handle_error 标 cancelled。
        - 其他状态(completed/failed/cancelled/pending): 返回 False,不可取消。
        - 未知 run_id(get_run 返回 None): 返回 False。
        """
        run = self._run_repo.get_run(run_id)
        if run is None:
            return False
        status = run["status"]

        if status == "interrupted":
            # interrupted 直接结束,无需 recompile
            self._run_repo.end_run(run_id, "cancelled")
            eid = self._audit_repo.add_event(run_id, "run_cancelled", "user")
            self._bus.publish(
                run_id,
                {
                    "id": eid,
                    "event_type": "run_cancelled",
                    "run_id": run_id,
                    "payload": {"reason": "user requested cancel"},
                },
            )
            self._cleanup_run(run_id)
            return True

        if status == "running":
            # set event 让 worker 检测
            event = self._cancel_events.get(run_id)
            if event is None:
                # _cancel_events 缺失(异常情况):无法协作取消
                return False
            event.set()
            # 标中间态 cancelling,等 worker 抛 RunCancelledError 后由 _handle_error 收尾
            self._run_repo.update_status(run_id, "cancelling")
            return True

        # completed / failed / cancelled / pending 等终态或不可取消状态
        return False

    def _cleanup_run(self, run_id: str) -> None:
        """清理已完成/失败的 run 的内存状态。

        interrupted 的 run 不清理——graph/config/threads 仍需用于 resume。
        """
        with self._lock:
            self._graphs.pop(run_id, None)
            self._configs.pop(run_id, None)
            self._threads.pop(run_id, None)
            self._cancel_events.pop(run_id, None)

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
        except BaseException as e:
            # 必须用 BaseException 而非 Exception:RunCancelledError 继承 BaseException
            # 以绕过 worker 内部 except Exception 吞没,改回 Exception 会静默破坏取消机制
            self._handle_error(run_id, e)

    def _resume_in_background(self, run_id: str, graph, config: dict, command) -> None:
        try:
            graph.invoke(command, config)
            self._handle_invoke_result(run_id, graph, config)
        except BaseException as e:
            # 必须用 BaseException 而非 Exception:RunCancelledError 继承 BaseException
            # 以绕过 worker 内部 except Exception 吞没,改回 Exception 会静默破坏取消机制
            self._handle_error(run_id, e)

    def _handle_invoke_result(self, run_id: str, graph, config: dict) -> None:
        # BUG-05 修复：get_state 可能因 checkpoint 损坏 / config 失效抛异常。
        # 若放任异常传播到 _run_in_background 的 except Exception，
        # 会调用 _handle_error 标记 run 为 failed。但 graph.invoke 已正常返回
        # 通常意味着 interrupted 或完成，误标 failed 会让用户无法 approve 续跑。
        # 保守标记为 interrupted 等待人工介入（用户 approve 时会再次尝试，
        # 若 resume 也失败则由 approve_run 的 except Exception 回滚为 failed）。
        try:
            state = graph.get_state(config)
        except Exception:
            self._run_repo.update_status(run_id, "interrupted")
            self._bus.publish(
                run_id, {"event_type": "run_interrupted", "run_id": run_id}
            )
            return
        if state.next:
            # interrupted：保留 graph/config/threads 供 resume 使用，不清理
            self._run_repo.update_status(run_id, "interrupted")
            self._bus.publish(run_id, {"event_type": "run_interrupted", "run_id": run_id})
        else:
            tokens = state.values.get("total_tokens", 0) if state.values else 0
            self._run_repo.end_run(run_id, "completed", total_tokens=tokens)
            eid = self._audit_repo.add_event(run_id, "run_end", "system")
            self._bus.publish(
                run_id, {"id": eid, "event_type": "run_end", "run_id": run_id}
            )
            self._cleanup_run(run_id)

    def _handle_error(self, run_id: str, error: BaseException) -> None:
        """统一处理 run 执行中的异常,根据异常类型标记终态并发布事件。

        分支:
        - RunCancelledError: 标 cancelled + 发 run_cancelled 事件(worker 检测到
          cancel event 后抛出,代表用户主动取消)
        - 其他异常: 标 failed + 发 error 事件(程序错误/LLM 异常等)

        两种分支都调用 _cleanup_run 释放 graph/config/threads/cancel_event 内存。
        """
        if isinstance(error, RunCancelledError):
            # 用户取消:标 cancelled + 发 run_cancelled 事件
            self._run_repo.end_run(run_id, "cancelled")
            eid = self._audit_repo.add_event(run_id, "run_cancelled", "user")
            self._bus.publish(
                run_id,
                {
                    "id": eid,
                    "event_type": "run_cancelled",
                    "run_id": run_id,
                    "payload": {"reason": "user requested cancel"},
                },
            )
        else:
            # 普通异常:沿用 failed 逻辑
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
