"""RunManager：后台线程执行 LangGraph graph + interrupt/resume。"""
from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait as _futures_wait
from typing import TYPE_CHECKING, Any, Callable

from agentteam.api.events import BroadcastTraceWriter, EventBus
from agentteam.config import get_settings
from agentteam.logging_config import get_logger
from agentteam.runtime.errors import RunCancelledError
from agentteam.storage.audit import AuditRepo
from agentteam.storage.runs import RunRepo

if TYPE_CHECKING:
    from agentteam.domain.team import Team
    from agentteam.runtime.graph import TeamCompiler

logger = get_logger("api.run_manager")

# 后台 run 线程池上限(防止 1000 并发 run 启 1000 线程压垮进程)。
# evolution 单独的线程池(避免 evolution 占满 run 线程池)。
# interrupted run TTL:超过该秒数未被 resume 的 interrupted run,
# 视为被遗弃,从内存驱逐 graph/config(节省内存,resume 时按 lazy recompile 路径重建)。
# 全部从集中式 Settings 读取(原 os.environ.get 已收敛到 agentteam.config)。
_settings = get_settings()
_MAX_RUN_WORKERS = _settings.max_run_workers
_MAX_EVOLUTION_WORKERS = _settings.max_evolution_workers
_INTERRUPTED_TTL_SECONDS = _settings.interrupted_ttl_seconds


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
        evolution_engine=None,
        max_run_workers: int = _MAX_RUN_WORKERS,
        max_evolution_workers: int = _MAX_EVOLUTION_WORKERS,
    ) -> None:
        self._run_repo = run_repo
        self._audit_repo = audit_repo
        self._bus = event_bus
        self._saver = checkpointer
        self._graphs: dict[str, Any] = {}
        self._configs: dict[str, dict] = {}
        # _threads 存储 Future(原为 threading.Thread)。
        # 保留 dict 名以维持测试兼容(tests/api/test_run_manager.py 检查
        # `run_id not in rm._threads` 验证 cleanup)。wait() 用 _futures_wait join。
        self._threads: dict[str, Future] = {}
        self._lock = threading.Lock()
        self._cancel_events: dict[str, threading.Event] = {}
        self._evolution = evolution_engine
        # interrupted_at:记录 run 进入 interrupted 状态的时间戳,
        # 用于 TTL 清理(避免 abandoned interrupted run 永久驻留内存)
        self._interrupted_at: dict[str, float] = {}
        # 有界线程池:替代裸 threading.Thread,防止高并发下 OS 线程爆炸。
        # 提交超限的 run 会在池内排队等待,而非立即失败。
        self._run_executor = ThreadPoolExecutor(
            max_workers=max_run_workers, thread_name_prefix="agentteam-run",
        )
        self._evolution_executor = ThreadPoolExecutor(
            max_workers=max_evolution_workers, thread_name_prefix="agentteam-evo",
        )

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
        """在后台线程中跑 graph.invoke()，立即返回。

        提交到有界 ThreadPoolExecutor 而非裸 threading.Thread:
        - 高并发下避免 1000 run 启 1000 线程压垮进程
        - 超限的 run 在池内排队等待,而非立即失败
        - shutdown 时统一 join,避免 daemon thread 被强杀丢数据
        """
        with self._lock:
            self._graphs[run_id] = graph
            self._configs[run_id] = config
            self._cancel_events[run_id] = threading.Event()
        self._run_repo.update_status(run_id, "running")
        future = self._run_executor.submit(
            self._run_in_background, run_id, graph, config, task
        )
        with self._lock:
            self._threads[run_id] = future

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
        # resume 走 run 线程池而非 evolution 线程池:resume 仍是 run 执行路径
        future = self._run_executor.submit(
            self._resume_in_background, run_id, graph, config,
            Command(resume=resume_value),
        )
        with self._lock:
            self._threads[run_id] = future

    def wait(self, run_id: str, timeout: float = 30.0) -> None:
        """等待 run 的后台 Future 结束(测试用)。

        用 _futures_wait 而非 future.result():后者会重抛执行异常,
        测试只关心 run 是否结束,不关心异常(异常已由 _handle_error 处理)。
        """
        with self._lock:
            future = self._threads.get(run_id)
        if future is not None:
            _futures_wait([future], timeout=timeout)

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
        - interrupted: try_claim("interrupted"→"cancelling") 原子转换,避免与
          approve_run 竞态;成功后 end_run("cancelled") + 发 run_cancelled 事件 + cleanup。
        - running: try_claim("running"→"cancelling") 原子转换,避免覆盖 worker 写入的
          终态;成功后 set cancel event,worker 在下一次 agent_step 检测后抛
          RunCancelledError,由 _handle_error 标 cancelled。
        - 其他状态(completed/failed/cancelled/pending): 返回 False,不可取消。
        - 未知 run_id(get_run 返回 None): 返回 False。

        竞态保护:与 approve_run 一致使用 try_claim 原子条件转换,
        避免 check-then-act 非原子导致的 status 覆盖(参见 project_memory 教训)。
        """
        run = self._run_repo.get_run(run_id)
        if run is None:
            return False
        status = run["status"]

        if status == "interrupted":
            # try_claim 原子转换:防止 approve_run 同时把 interrupted → running
            if not self._run_repo.try_claim(run_id, "interrupted", "cancelling"):
                return False  # 被 approve 抢走或状态已变
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
            # try_claim 原子转换:防止 worker 自然完成时 update_status("interrupted"/"completed")被覆盖
            if not self._run_repo.try_claim(run_id, "running", "cancelling"):
                return False  # 状态已变(worker 已结束或被其他请求取消)
            event = self._cancel_events.get(run_id)
            if event is None:
                # 异常:claim 成功但 event 缺失,回滚状态避免卡在 cancelling
                self._run_repo.update_status(run_id, "running")
                return False
            event.set()
            # worker 检测到 event 后抛 RunCancelledError,由 _handle_error 标 cancelled
            return True

        # completed / failed / cancelled / pending 等终态或不可取消状态
        return False

    def _cleanup_run(self, run_id: str) -> None:
        """清理已完成/失败的 run 的内存状态。

        interrupted 的 run 不清理——graph/config/threads 仍需用于 resume。
        _interrupted_at 也仅在 run 真正离开 interrupted 状态(被 resume/cancel/结束)
        时才清除,与 graph/config 同步。
        """
        with self._lock:
            self._graphs.pop(run_id, None)
            self._configs.pop(run_id, None)
            self._threads.pop(run_id, None)
            self._cancel_events.pop(run_id, None)
            self._interrupted_at.pop(run_id, None)

    def _sweep_interrupted_runs(self) -> int:
        """驱逐超过 TTL 仍未被 resume 的 interrupted run 的内存态。

        被驱逐的 run 若之后被 approve,approve_run 检测到 has_graph=False
        会走 lazy recompile 路径重建 graph,因此驱逐是安全的。

        返回:被驱逐的 run 数(供运维观测)。

        线程安全:与 _cleanup_run 一致用 self._lock 保护 dict 修改。
        数据库状态不动:仅清理内存态,DB 中 status 仍为 interrupted,
        若用户长时间不 approve,这是预期行为(用户可见 status=interrupted,
        approve 时触发 recompile)。

        TTL=0(_INTERRUPTED_TTL_SECONDS=0)时禁用清理,直接返回 0。
        """
        if _INTERRUPTED_TTL_SECONDS <= 0:
            return 0
        now = time.time()
        expired: list[str] = []
        with self._lock:
            for run_id, ts in self._interrupted_at.items():
                if now - ts > _INTERRUPTED_TTL_SECONDS:
                    expired.append(run_id)
            for run_id in expired:
                self._graphs.pop(run_id, None)
                self._configs.pop(run_id, None)
                self._threads.pop(run_id, None)
                self._cancel_events.pop(run_id, None)
                self._interrupted_at.pop(run_id, None)
        if expired:
            logger.info(
                "sweep_interrupted_runs: evicted %d abandoned runs (TTL=%ds): %s",
                len(expired), _INTERRUPTED_TTL_SECONDS, expired,
            )
        return len(expired)

    def shutdown(self, wait: bool = True) -> None:
        """关闭后台线程池,释放资源。

        在 server.py lifespan 的 shutdown 阶段调用,确保进程退出时
        所有 run/evolution 后台任务有机会完成或被取消。

        参数:
            wait: True=等待正在执行的任务完成(优雅停机);
                  False=立即取消排队中的任务(快速停机)。
        """
        # 先驱逐 interrupted run 的内存态,避免 shutdown 期间状态不一致
        self._sweep_interrupted_runs()
        self._run_executor.shutdown(wait=wait, cancel_futures=not wait)
        self._evolution_executor.shutdown(wait=wait, cancel_futures=not wait)

    def _trigger_evolution_async(self, run_id: str) -> None:
        """异步触发 EvolutionEngine(SP7b)。

        提交到 evolution_executor(独立小池):不阻塞 API 响应,
        失败不影响 run 结果。evolution_engine=None 时静默跳过(向后兼容)。
        单独线程池避免 evolution 占满 run 线程池导致新 run 排队。

        异常隔离:trigger 内部各维度已有 try/except,但 trigger() 顶层
        仍可能抛异常(get_run DB 异常 / update_version / skill_loader.reload),
        用 _safe_trigger wrapper 捕获所有异常并记录日志,
        保持与 _run_in_background 的 try/except 风格一致。
        """
        if self._evolution is None:
            return

        def _safe_trigger() -> None:
            try:
                self._evolution.trigger(run_id)
            except Exception:
                # executor 异常不应影响 run 已标记的终态,但必须记录日志以便排障
                logger.exception("evolution trigger failed for run %s", run_id)

        self._evolution_executor.submit(_safe_trigger)

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
            logger.exception("get_state failed for run %s, marking interrupted", run_id)
            self._run_repo.update_status(run_id, "interrupted")
            self._mark_interrupted(run_id)
            self._bus.publish(
                run_id, {"event_type": "run_interrupted", "run_id": run_id}
            )
            return
        if state.next:
            # interrupted：保留 graph/config/threads 供 resume 使用，不清理。
            # _mark_interrupted 记录时间戳供 _sweep_interrupted_runs TTL 驱逐。
            self._run_repo.update_status(run_id, "interrupted")
            self._mark_interrupted(run_id)
            self._bus.publish(run_id, {"event_type": "run_interrupted", "run_id": run_id})
        else:
            tokens = state.values.get("total_tokens", 0) if state.values else 0
            self._run_repo.end_run(run_id, "completed", total_tokens=tokens)
            eid = self._audit_repo.add_event(run_id, "run_end", "system")
            self._bus.publish(
                run_id, {"id": eid, "event_type": "run_end", "run_id": run_id}
            )
            self._cleanup_run(run_id)
            # SP7b: completed 后异步触发进化
            self._trigger_evolution_async(run_id)

    def _mark_interrupted(self, run_id: str) -> None:
        """记录 run 进入 interrupted 状态的时间戳(供 TTL 清理)。

        抽成 helper 而非内联:被 _handle_invoke_result 的两个 interrupted 分支
        (get_state 失败 + state.next 非空)共用,避免时间戳记录分散遗漏。
        与 _cleanup_run/_sweep_interrupted_runs 协同:
        - mark: 进入 interrupted 时记 now()
        - pop: 离开 interrupted(resume/cancel/结束)时清
        - sweep: 超过 TTL 的 mark 残留 → 驱逐内存态
        """
        with self._lock:
            self._interrupted_at[run_id] = time.time()

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
            logger.info("run %s cancelled by user", run_id)
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
            logger.exception("run %s failed", run_id, exc_info=error)
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
        # SP7b: failed 后异步触发进化(cancelled 不触发)。
        # 触发顺序与 _handle_invoke_result 对称:cleanup 之后,确保内存态已释放。
        if not isinstance(error, RunCancelledError):
            self._trigger_evolution_async(run_id)
