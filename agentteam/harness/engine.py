"""HarnessRunner：opencode v2 会话编排引擎（SP8 核心）。

把 AgentTeam 的 supervisor 编排语义（plan→dispatch→review，sequential/dag，
三级审批）映射到 opencode v2 `/api/*` 会话原语上，并以 graph 协议（invoke/get_state）
接入现有 RunManager 生命周期（interrupt/resume/cancel/进化触发全复用）。

v2 迁移带来的三处实现差异（实测依据见 docs/opencode-harness-design.md §8）：
- 只有异步 prompt：`POST /api/session/{id}/prompt` + 轮询 `finish=="stop"`
  （v1 的同步 /message 与 per-request system/format/tools 都不存在）。
  system prompt 由 translator 内联进用户文本，计划 JSON 由 prompt 内嵌 schema
  + `translator.extract_json` 容错提取。
- tool 级审批从「事前规则集」改为「事后中断 + 审计」：引擎在等待循环里消费
  EventMapper 观测到的工具调用，命中白名单外/审批目标即 POST interrupt 终止
  回合、发 tool_denied 并 park 等人工（代价：首个调用可能已执行完）。
- token 用量取逐消息 `tokens` 求和（v2 会话对象聚合恒为 0）。

可恢复性设计（与 LangGraph SqliteSaver checkpoint 对等）：
- 编排状态是**纯 JSON 可序列化**的 frame 栈 + stage 状态机。
  park（审批等待人工）时整体快照 → ApprovalInterrupt → invoke return →
  RunManager 标 interrupted；resume 时 invoke({"__resume__": ...}) 重入，
  先 resolve 决策（审计落账；tool 门还要向会话补发「已批准，继续」）再推进。
- 快照经 state_store 持久化到 SQLite（run_engine_state 表），
  服务重启后 approve 走 rehydrate 路径重建 runner 再续跑。
- dag 并行 fan-out 由 opencode 服务端并发执行，主线程单点轮询；park 时全部
  在飞会话 id 已在 frame["round"] 快照内，resume 后对已完成的会话直接取结果
  （opencode 会话在服务端存活，幂等）。

stage 状态机（每 frame 一个 stage，_advance 每次推进一步）：
  sequential: gate_step → gate_worker → run → review → gate_step → ... → done
  dag:        round_gate → dispatch_round → round_wait → round_review → ... → done
park ctx 记录 stage + frame_idx；resume 时 _apply_resume 推进 stage
（gate 类）或补发续跑（tool 类），drive 循环从断点继续。

与 LangGraph 引擎的语义对齐见 docs/opencode-harness-design.md §3.2。
"""
from __future__ import annotations

import threading
import time
from typing import Any

from agentteam.api.run_manager import RunCancelledError
from agentteam.domain.agent import Agent, TeamRef
from agentteam.domain.approval import ApprovalPolicy
from agentteam.harness import translator
from agentteam.harness.approval import ApprovalBroker, ApprovalInterrupt
from agentteam.harness.events import EventMapper
from agentteam.harness.opencode_client import OpenCodeClient, OpenCodeError
from agentteam.runtime.trace import TraceWriter

# Plan JSON schema：v2 没有结构化输出通道（format=json_schema 是 v1 能力），
# 它被序列化进 prompt 文本作为硬约束，由 translator.extract_json 容错提取。
# 语义与 runtime.nodes.Plan 保持一致
# （worker/instruction/id/depends_on/condition/execution_mode）。
_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "worker": {"type": "string",
                               "description": "执行此步的 worker name"},
                    "instruction": {"type": "string",
                                    "description": "子任务描述"},
                    "id": {"type": "string",
                           "description": "唯一 id(空=用 worker 名)"},
                    "depends_on": {"type": "array",
                                   "items": {"type": "string"},
                                   "description": "依赖的 step id 列表"},
                    "condition": {"type": "string",
                                  "description": "Python 表达式,求值 False 则跳过"},
                },
                "required": ["worker", "instruction"],
                "additionalProperties": False,
            },
        },
        "execution_mode": {
            "type": "string",
            "enum": ["sequential", "dag"],
            "description": "执行模式",
        },
    },
    "required": ["steps", "execution_mode"],
    "additionalProperties": False,
}


def _policy_dict(policy: ApprovalPolicy | None) -> dict | None:
    if policy is None:
        return None
    return {
        "level": policy.level,
        "targets": list(policy.targets) if policy.targets is not None else None,
        "timeout_seconds": policy.timeout_seconds,
    }


def _policy_from(d: dict | None) -> ApprovalPolicy | None:
    if d is None:
        return None
    return ApprovalPolicy(
        level=d["level"],
        targets=list(d["targets"]) if d.get("targets") is not None else None,
        timeout_seconds=d.get("timeout_seconds"),
    )


def _model_dict(ref) -> dict | None:
    if ref is None:
        return None
    return {"provider": ref.provider, "name": ref.name,
            "temperature": ref.temperature, "streaming": ref.streaming}


def _model_from(d: dict | None):
    if d is None:
        return None
    from agentteam.models.provider import ModelRef
    return ModelRef(provider=d["provider"], name=d["name"],
                    temperature=d.get("temperature", 0.7),
                    streaming=d.get("streaming", True))


class _StateView:
    """graph 协议的 get_state() 返回物（duck type LangGraph StateSnapshot）。"""

    def __init__(self, values: dict, has_next: bool) -> None:
        self.values = values
        self.next = ("parked",) if has_next else ()


class HarnessRunner:
    """opencode 执行引擎。实现 graph 协议：invoke / get_state。

    生命周期由 RunManager 驱动：
    - start:  invoke(initial, config)   → 正常执行或 park 后 return
    - resume: invoke({"__resume__": ...}, config)（RunManager 分支）
    - 终态:   get_state(config).next 为空 → completed；非空 → interrupted
    """

    # RunManager 用它区分 harness 引擎与 LangGraph 图（避免 import 环）
    __harness__ = True

    def __init__(
        self,
        *,
        client: OpenCodeClient,
        run_id: str,
        team,
        task: str,
        trace_writer: TraceWriter | None,
        audit_repo=None,
        skill_loader=None,
        library=None,
        team_registry: dict[str, Any] | None = None,
        run_manager=None,
        state_store=None,
        default_model: str = "opencode/ling-3.0-flash-fin-free",
        prompt_timeout: float = 600.0,
        poll_interval: float = 0.25,
    ) -> None:
        self._client = client
        self._run_id = run_id
        self._team = team
        self._task = task
        self._trace = trace_writer
        self._audit = audit_repo
        self._skills = skill_loader
        self._library = library
        self._teams = team_registry or {}
        self._rm = run_manager
        self._store = state_store
        self._default_model = default_model
        self._prompt_timeout = prompt_timeout
        self._poll = poll_interval
        self._broker = ApprovalBroker(trace_writer, audit_repo)
        self._mapper = EventMapper(run_id, trace_writer, client)
        self._state: dict[str, Any] | None = None

    # ================= graph 协议 =================

    def invoke(self, initial: dict, config: dict) -> dict:
        """graph 协议入口。initial 为初始 state 或 {"__resume__": decision}。"""
        resume = initial.get("__resume__") if isinstance(initial, dict) else None
        self._mapper.start()
        completed_fully = False
        try:
            if resume is not None:
                if self._state is None:
                    self._state = self._load_state()
                if self._state is None:
                    raise ValueError(
                        f"Run {self._run_id}: harness state not found (snapshot lost)"
                    )
                self._apply_resume(self._state, resume)
            else:
                self._state = self._initial_state()
                self._persist(self._state)
            self._drive(self._state)
            completed_fully = (
                self._state.get("park") is None
                and all(f["phase"] == "done" for f in self._state["frames"])
            )
            return {"total_tokens": self._state.get("total_tokens", 0)}
        finally:
            self._mapper.stop()
            if completed_fully and self._store is not None:
                self._store.clear(self._run_id)
            elif self._state is not None:
                self._persist(self._state)

    def get_state(self, config: dict) -> _StateView:
        st = self._state
        parked = bool(st and st.get("park"))
        return _StateView(
            {"total_tokens": (st or {}).get("total_tokens", 0)}, parked
        )

    # ================= 状态构造 / 快照 =================

    def _initial_state(self) -> dict:
        root = self._team.root
        if self._library is not None:
            root = self._library.resolve(root)
        # 编译期校验 parity：root 必须是有 children 的 supervisor
        # （TeamCompiler._validate 同款约束）
        if root.role != "supervisor" or not root.children:
            raise ValueError(
                f"Team '{self._team.name}': harness engine requires "
                f"a supervisor root with children"
            )
        return {
            "task": self._task,
            "worker_outputs": {},
            "total_tokens": 0,
            "frames": [self._frame_from_agent(root, self._task)],
            "park": None,
            # session_id → 人工已放行的工具名（事后审批不重复 park 同一工具）
            "approved_tools": {},
        }

    def _frame_from_agent(self, agent: Agent, instruction: str) -> dict:
        skills = self._skills.load(agent.skills) if (self._skills and agent.skills) else {}
        return {
            "agent_name": agent.name,
            "system_prompt": agent.system_prompt,
            "skills_content": skills,
            "model": _model_dict(agent.model),
            "policy": _policy_dict(agent.approval_policy),
            "instruction": instruction,
            "children": [self._child_spec(c) for c in agent.children],
            "phase": "plan",
            "plan": [],
            "mode": "sequential",
            "current": 0,
            "completed": [],
            "skipped": [],
            "rejected": False,
            "result": "",
            "stage": None,
            "round": {},
            "inflight": None,
        }

    def _child_spec(self, child: Agent | TeamRef) -> dict:
        """Agent/TeamRef → 可序列化 child spec（TeamRef 展开为被引 Team 的 root）。"""
        if isinstance(child, TeamRef):
            target = self._teams.get(child.name)
            if target is None:
                raise ValueError(
                    f"TeamRef '{child.name}' not registered (harness engine)"
                )
            root = target.root
            if self._library is not None:
                root = self._library.resolve(root)
            frame = self._frame_from_agent(root, "")
            frame["agent_name"] = child.alias or child.name
            return {"kind": "supervisor", "name": child.alias or child.name,
                    "frame": frame}
        if child.ref and self._library is not None:
            child = self._library.resolve(child)
        if child.role == "supervisor":
            frame = self._frame_from_agent(child, "")
            return {"kind": "supervisor", "name": child.name, "frame": frame}
        skills = self._skills.load(child.skills) if (self._skills and child.skills) else {}
        return {
            "kind": "worker",
            "name": child.name,
            "system_prompt": child.system_prompt,
            "skills_content": skills,
            "model": _model_dict(child.model),
            "policy": _policy_dict(child.approval_policy),
            "tools": list(child.tools),
        }

    def _persist(self, st: dict) -> None:
        if self._store is not None:
            self._store.save(self._run_id, st)

    def _load_state(self) -> dict | None:
        if self._store is None:
            return None
        return self._store.load(self._run_id)

    # ================= resume =================

    def _apply_resume(self, st: dict, decision: dict) -> None:
        """resolve 审批决策并推进断点状态机（tool 门还要向会话补发续跑 prompt）。"""
        park = st.get("park")
        if park is None:
            raise ValueError(f"Run {self._run_id}: resume without pending park")
        gate = park["gate"]
        ctx = park.get("ctx") or {}
        approved = self._broker.resolve(
            self._run_id, gate, bool(decision.get("approved")),
            decision.get("decider", "api-user"), decision.get("reason"),
        )
        st["park"] = None
        stage = ctx.get("stage")
        # 拒绝 → frame 置 rejected + 终止（LangGraph parity：is_rejected → 全路由 END）
        if not approved:
            frame = st["frames"][ctx["frame_idx"]]
            frame["rejected"] = True
            frame["phase"] = "done"
            return
        if stage == "gate_step":
            st["frames"][ctx["frame_idx"]]["stage"] = "gate_worker"
            return
        if stage == "gate_worker":
            st["frames"][ctx["frame_idx"]]["stage"] = "run"
            return
        if stage == "round_gate":
            st["frames"][ctx["frame_idx"]]["stage"] = "dispatch_round"
            return
        if stage in ("tool", "round_tool"):
            # 回合已被引擎 interrupt 掉，必须补发一轮 prompt 才会继续产出；
            # 已批准的同类工具记入快照，避免同一工具反复 park。
            session_id = ctx["session_id"]
            tool = gate.get("permission") or ""
            granted = st.setdefault("approved_tools", {})
            granted.setdefault(session_id, [])
            if tool and tool not in granted[session_id]:
                granted[session_id].append(tool)
            resp = self._client.prompt_async(
                session_id, translator.approved_resume_prompt(tool, gate.get("worker")),
                delivery="queue",
            )
            # stage 不变：seq 停在 frame["inflight"]/run，
            # dag 停在 round_wait —— drive 循环重入继续等待即可；
            # 但回合完成度的锚点要换成这一轮的用户消息 id。
            info = self._parked_info(st, ctx)
            if info is not None:
                info["turn_id"] = self._client.turn_message_id(resp)
            return
        raise ValueError(f"unknown park stage: {stage}")

    @staticmethod
    def _parked_info(st: dict, ctx: dict) -> dict | None:
        """park ctx → 在飞会话记录（seq 在 frame["inflight"]，dag 在 round[key]）。"""
        frame = st["frames"][ctx["frame_idx"]]
        if ctx["stage"] == "round_tool":
            return (frame.get("round") or {}).get(ctx.get("key"))
        return frame.get("inflight")

    # ================= 主驱动（断点状态机） =================

    def _drive(self, st: dict) -> None:
        """逐 stage 推进 frame 栈直到根 frame 完成或 park。"""
        while True:
            self._check_cancel()
            top_idx = None
            for i in range(len(st["frames"]) - 1, -1, -1):
                if st["frames"][i]["phase"] != "done":
                    top_idx = i
                    break
            if top_idx is None:
                return
            frame = st["frames"][top_idx]
            try:
                self._advance(st, frame, top_idx)
                self._persist(st)
            except ApprovalInterrupt as intr:
                # _raise_park（tool 门）已写 st["park"]（ctx 完整）；
                # gate 类门（request_gate 抛出）在此补 ctx
                if st.get("park") is None:
                    st["park"] = {
                        "gate": intr.gate,
                        "ctx": {"stage": frame.get("stage"), "frame_idx": top_idx},
                    }
                self._persist(st)
                return  # invoke return → RunManager 标 interrupted

    def _advance(self, st: dict, frame: dict, idx: int) -> None:
        """推进一步。审批需人工时抛 ApprovalInterrupt。"""
        if frame["phase"] == "plan":
            self._do_plan(st, frame)
            return
        if frame["phase"] == "dispatch":
            if frame["mode"] == "sequential":
                self._dispatch_seq(st, frame, idx)
            else:
                self._dispatch_dag(st, frame, idx)
            return
        if frame["phase"] == "review":
            self._do_review(st, frame)
            return
        # phase == done（防御：_drive 只挑非 done frame）

    # ---------- plan ----------

    def _do_plan(self, st: dict, frame: dict) -> None:
        roster = [c["name"] for c in frame["children"]]
        text = translator.plan_prompt(
            _PLAN_SCHEMA, roster, frame["instruction"] or st["task"]
        )
        answer = self._one_shot(frame, text)
        plan_obj = self._parse_plan(answer)
        frame["plan"] = [
            {
                "worker": s["worker"],
                "instruction": s["instruction"],
                "status": "pending",
                "id": s.get("id") or s["worker"],
                "depends_on": list(s.get("depends_on") or []),
                "condition": s.get("condition"),
            }
            for s in plan_obj["steps"]
        ]
        frame["mode"] = plan_obj.get("execution_mode", "sequential")
        self._validate_plan(frame)
        if self._trace is not None:
            self._trace.emit(
                self._run_id, "leader_plan", frame["agent_name"],
                {"steps": len(frame["plan"])},
            )
        frame["phase"] = "dispatch"

    @staticmethod
    def _parse_plan(answer: str) -> dict:
        try:
            obj = translator.extract_json(answer)
        except ValueError as e:
            raise ValueError(
                f"Leader plan is not valid JSON: {e}; got: {answer[:200]}"
            ) from e
        if not isinstance(obj.get("steps"), list):
            raise ValueError(f"Leader plan JSON must contain steps[]: {answer[:200]}")
        return obj

    @staticmethod
    def _validate_plan(frame: dict) -> None:
        """dag 校验 parity：step id 唯一 + 循环依赖拒绝（复用 runtime 纯函数）。"""
        if frame["mode"] != "dag":
            return
        ids = [s["id"] for s in frame["plan"]]
        duplicates = {sid for sid in ids if ids.count(sid) > 1}
        if duplicates:
            raise ValueError(
                f"Plan has duplicate step ids in dag mode: {sorted(duplicates)}"
            )
        from agentteam.runtime.graph import _detect_dag_cycle
        if _detect_dag_cycle(frame["plan"]):
            raise ValueError(
                f"Plan has circular dependency in dag mode: "
                f"{[s['id'] for s in frame['plan']]}"
            )

    # ---------- sequential dispatch ----------

    def _dispatch_seq(self, st: dict, frame: dict, idx: int) -> None:
        if frame["rejected"]:
            # 拒绝 → 整个 run 终止（LangGraph parity：is_rejected → 全路由 END）
            frame["phase"] = "done"
            return
        stage = frame["stage"]
        if stage is None:
            if frame["current"] >= len(frame["plan"]):
                frame["phase"] = "done"
                return
            frame["stage"] = "gate_step"
            stage = "gate_step"
        if stage == "gate_step":
            self._broker.request_gate(
                self._run_id, _policy_from(frame["policy"]), "step",
                target=str(frame["current"]),
            )
            frame["stage"] = "gate_worker"
            return
        if stage == "gate_worker":
            step = frame["plan"][frame["current"]]
            child = self._find_child(frame, step["worker"])
            self._broker.request_gate(
                self._run_id, _policy_from(child.get("policy")), "worker",
                target=child["name"],
            )
            frame["stage"] = "run"
            return
        if stage == "run":
            step = frame["plan"][frame["current"]]
            child = self._find_child(frame, step["worker"])
            if child["kind"] == "supervisor":
                sub = child["frame"]
                sub["instruction"] = step["instruction"]
                sub["phase"] = "plan"
                st["frames"].append(sub)
                frame["stage"] = "await_child"
                return
            inflight = frame.get("inflight")
            if inflight is None:
                answer = self._start_worker(
                    st, frame, idx, child, step["instruction"],
                    park_stage="tool", step_index=frame["current"],
                )
            else:
                answer = self._wait_worker_session(
                    st, frame, idx, child, inflight,
                    park_stage="run",
                )
            self._finish_seq_step(st, frame, child, answer)
            return
        if stage == "await_child":
            sub = st["frames"][-1]
            if sub.get("rejected"):
                # 子团队被拒 → 向父 frame 传播，run 终止
                frame["rejected"] = True
                frame["phase"] = "done"
                return
            child = self._find_child(
                frame, frame["plan"][frame["current"]]["worker"]
            )
            answer = sub.get("result") or ""
            self._finish_seq_step(st, frame, child, answer)
            return
        if stage == "review":
            self._do_review(st, frame)
            return
        raise ValueError(f"unknown sequential stage: {stage}")

    def _finish_seq_step(self, st: dict, frame: dict, child: dict, answer: str) -> None:
        st["worker_outputs"][child["name"]] = answer
        frame["plan"][frame["current"]]["status"] = "done"
        frame["current"] += 1
        frame["inflight"] = None
        frame["stage"] = "review"

    # ---------- dag dispatch ----------

    def _dispatch_dag(self, st: dict, frame: dict, idx: int) -> None:
        if frame["rejected"]:
            frame["phase"] = "done"
            return
        stage = frame["stage"]
        if stage is None:
            if all(s["status"] in ("done", "skipped") for s in frame["plan"]):
                frame["phase"] = "done"
                return
            frame["stage"] = "round_gate"
            stage = "round_gate"
        if stage == "round_gate":
            ready = self._ready_steps(st, frame)
            if not ready["steps"]:
                # LangGraph parity：路由空就绪集返回 [END]——剩余 step 的依赖
                # 不可满足（或全部 done/skipped），终止而非空转
                frame["phase"] = "done"
                return
            self._broker.request_gate(
                self._run_id, _policy_from(frame["policy"]), "step",
                target=",".join(ready["ids"]),
            )
            frame["stage"] = "dispatch_round"
            return
        if stage == "dispatch_round":
            ready = self._ready_steps(st, frame)
            # worker 级门（batch parity：一轮内任一 worker 需审批 → 整轮 park，
            # resume 决策作用于全部，与 LangGraph 多 interrupt 共享 resume 值一致）
            for step in ready["steps"]:
                child = self._find_child(frame, step["worker"])
                if child["kind"] != "worker":
                    continue
                self._broker.request_gate(
                    self._run_id, _policy_from(child.get("policy")), "worker",
                    target=child["name"],
                )
            frame["round"] = {}
            for step in ready["steps"]:
                child = self._find_child(frame, step["worker"])
                if child["kind"] == "supervisor":
                    sub = child["frame"]
                    sub["instruction"] = step["instruction"]
                    sub["phase"] = "plan"
                    st["frames"].append(sub)
                    frame["round"][step["id"]] = {
                        "worker": child["name"], "session_id": None,
                        "instruction": step["instruction"], "subteam": True,
                    }
                else:
                    sess = self._create_worker_session(child)
                    self._mapper.register_session(sess["id"], child["name"])
                    if self._trace is not None:
                        self._trace.emit(self._run_id, "worker_start", child["name"])
                    resp = self._client.prompt_async(
                        sess["id"],
                        translator.worker_user_prompt(child, step["instruction"]),
                        delivery="queue",
                    )
                    frame["round"][step["id"]] = {
                        "worker": child["name"], "session_id": sess["id"],
                        "instruction": step["instruction"],
                        "turn_id": self._client.turn_message_id(resp),
                    }
            frame["stage"] = "round_wait"
            return
        if stage == "round_wait":
            self._wait_round(st, frame, idx)
            frame["stage"] = "round_review"
            return
        if stage == "round_review":
            self._do_review(st, frame)
            return
        raise ValueError(f"unknown dag stage: {stage}")

    def _ready_steps(self, st: dict, frame: dict) -> dict:
        """拓扑就绪集 + condition 求值（False → in-place skipped，parity）。

        依赖满足判定与 LangGraph 引擎对齐（graph.make_route_from_plan_dag）：
        completed **或 skipped** 均视为满足，避免被跳过的 step 阻塞后继。
        """
        completed = set(frame["completed"])
        skipped = set(frame["skipped"])
        ready: list[dict] = []
        for step in frame["plan"]:
            if step["status"] != "pending":
                continue
            if not all(d in completed or d in skipped
                       for d in step["depends_on"]):
                continue
            if step.get("condition"):
                from agentteam.runtime.graph import _eval_condition
                if not _eval_condition(step["condition"], {
                    "worker_outputs": st["worker_outputs"],
                    "completed_steps": completed,
                    "skipped_steps": skipped,
                    "task": st["task"],
                }):
                    step["status"] = "skipped"
                    skipped.add(step["id"])
                    continue
            ready.append(step)
        return {"steps": ready, "ids": [s["id"] for s in ready]}

    def _wait_round(self, st: dict, frame: dict, idx: int) -> None:
        """轮询等待一轮会话完成 + 收集 subteam 结果。

        主线程单点轮询全部在飞会话（opencode 服务端本就并行执行）：
        park/取消/超时都在本线程处理，无线程池 join 阻塞。
        park 时全部在飞会话 id 已在 frame["round"] 内（快照携带），
        resume 后重入本方法：已 idle 的会话直接取结果，未决的继续等。
        """
        remaining = {
            step_id: info for step_id, info in frame["round"].items()
            if info.get("session_id") and info.get("answer") is None
        }
        self._wait_sessions(st, frame, idx, remaining, park_stage="round_tool")
        # subteam step：对应子 frame 已 done（栈内更早完成），取其 result
        for step_id, info in frame["round"].items():
            if info.get("subteam") and info.get("answer") is None:
                sub = self._find_done_subframe(st, info)
                info["answer"] = sub.get("result") or ""
                if self._trace is not None:
                    self._trace.emit(
                        self._run_id, "worker_end", info["worker"],
                        {"answer_length": len(info["answer"])},
                    )
        # 收集进 outputs / plan 状态
        for step_id, info in frame["round"].items():
            if info.get("answer") is None:
                continue
            st["worker_outputs"][info["worker"]] = info["answer"]
            for step in frame["plan"]:
                if step["id"] == step_id and step["status"] == "pending":
                    step["status"] = "done"
            if step_id not in frame["completed"]:
                frame["completed"].append(step_id)

    def _find_done_subframe(self, st: dict, info: dict) -> dict:
        for f in reversed(st["frames"]):
            if (f["phase"] == "done" and f["agent_name"] == info["worker"]
                    and f["instruction"] == info["instruction"]):
                return f
        raise ValueError(
            f"subteam frame for step worker '{info['worker']}' not found/done"
        )

    # ---------- worker 会话 ----------

    def _create_worker_session(self, child: dict) -> dict:
        """v2 会话只接受 model / agent —— 权限与工具白名单没有会话级入口（§8）。

        `agent` 传 worker 名当标签用：v1.18.32 对未注册的 agent 名照单收下并
        原样回显（实测），既方便在 opencode 侧按 worker 辨认会话，也是
        「将来 v2 agent 配置生效时直接对上」的前置。
        """
        return self._client.create_session(
            model=self._model_for(child), agent=child["name"]
        )

    def _start_worker(
        self, st: dict, frame: dict, idx: int, child: dict,
        instruction: str, park_stage: str, step_index: int | None = None,
        step_id: str | None = None,
    ) -> str:
        """创建会话 + 异步 prompt + 等待完成（seq 路径）。返回最终文本。"""
        sess = self._create_worker_session(child)
        self._mapper.register_session(sess["id"], child["name"])
        if self._trace is not None:
            self._trace.emit(self._run_id, "worker_start", child["name"])
        resp = self._client.prompt_async(
            sess["id"],
            translator.worker_user_prompt(child, instruction),
            delivery="queue",
        )
        info = {
            "session_id": sess["id"], "worker": child["name"],
            "instruction": instruction, "step_index": step_index,
            "step_id": step_id,
            "turn_id": self._client.turn_message_id(resp),
        }
        frame["inflight"] = info
        self._persist(st)
        return self._wait_worker_session(st, frame, idx, child, info,
                                         park_stage=park_stage)

    def _wait_worker_session(
        self, st: dict, frame: dict, idx: int, child: dict, info: dict,
        park_stage: str = "tool", step_id: str | None = None,
    ) -> str:
        """seq 路径：等待单个在飞会话完成（见 _wait_sessions）。

        park_stage 用 resume 处理器命名（"tool"），frame["stage"]（"run"）
        由 drive 循环独立用于重入。
        """
        self._wait_sessions(st, frame, idx, {"__seq__": info},
                            park_stage=park_stage)
        return info.get("answer", "")

    def _wait_sessions(
        self, st: dict, frame: dict, idx: int, remaining: dict,
        park_stage: str,
    ) -> None:
        """主线程轮询多会话直至全部完成；取消/工具违规/错误三路处理。

        remaining: {key: info}，info 含 session_id/worker；完成的写 info["answer"]。
        完成判定 = 最后一条 assistant 消息 finish=="stop"（v2 没有 session.idle
        契约、/wait 返回 503，SSE 事件只用来少睡一轮）。
        工具违规 = **事后中断**：观测到新工具调用 → broker 判定 → 需人工时
        POST interrupt 掐断回合再 park（首个调用可能已执行完，见 approval.py）。
        """
        deadline = time.monotonic() + self._prompt_timeout
        while remaining:
            if self._rm is not None and self._rm.is_cancelled(self._run_id):
                for info in remaining.values():
                    self._interrupt_quietly(info["session_id"])
                raise RunCancelledError()
            for key, info in list(remaining.items()):
                session_id = info["session_id"]
                # SSE 侧 session.error/step.failed 之外再走一条 REST 判定：
                # 实测限流回合只落 assistant finish=="error"（见 turn_error docstring），
                # 漏掉它会让 run 吊到 prompt 超时（默认 600s）而不是立刻失败。
                err = (self._mapper.session_error(session_id)
                       or self._client.turn_error(session_id, info.get("turn_id")))
                if err:
                    raise OpenCodeError(
                        f"opencode session {session_id} failed: {err}"
                    )
                child = self._find_child(frame, info["worker"])
                self._review_tool_calls(
                    st, frame, idx, child, info, park_stage, key)
                turn_id = info.get("turn_id")
                if self._client.assistant_done(session_id, turn_id) is None:
                    continue
                # 收尾对账：REST 的完成信号可能领先于 SSE 观测，直接用 durable
                # history 补一次再判违规，否则「事后中断+审计」会整批发漏
                # （seq 去重保证不重复计 token / 不重复入观测队列）。
                self._reconcile(session_id)
                self._review_tool_calls(
                    st, frame, idx, child, info, park_stage, key)
                self._accrue_tokens(session_id)
                info["answer"] = self._client.final_text(session_id, turn_id)
                if self._trace is not None:
                    self._trace.emit(
                        self._run_id, "worker_end", info["worker"],
                        {"answer_length": len(info["answer"])},
                    )
                remaining.pop(key)
            if not remaining:
                return
            if time.monotonic() > deadline:
                for info in remaining.values():
                    self._interrupt_quietly(info["session_id"])
                raise OpenCodeError(
                    f"opencode sessions timed out after {self._prompt_timeout}s: "
                    f"{[i['session_id'] for i in remaining.values()]}"
                )
            time.sleep(self._poll)

    def _review_tool_calls(
        self, st: dict, frame: dict, idx: int, child: dict, info: dict,
        park_stage: str, key: str,
    ) -> None:
        """消费本会话新观测到的工具调用；命中违规即中断 + park。"""
        session_id = info["session_id"]
        new_calls = self._mapper.take_new_calls(session_id)
        if not new_calls:
            return
        policy = _policy_from(child.get("policy"))
        whitelist = translator.worker_tool_whitelist(child.get("tools") or [])
        granted = st.get("approved_tools", {}).get(session_id, [])
        for i, call in enumerate(new_calls):
            if call.name in granted:
                continue
            try:
                self._broker.review_tool_call(
                    self._run_id, policy, child["name"], call, session_id, whitelist
                )
            except ApprovalInterrupt as intr:
                # 本批尚未判定的调用退回队列，resume 后继续处理
                self._mapper.requeue_calls(session_id, new_calls[i + 1:])
                self._interrupt_quietly(session_id)
                self._raise_park(st, {
                    "stage": park_stage, "frame_idx": idx,
                    "session_id": session_id, "key": key,
                }, intr.gate)

    def _interrupt_quietly(self, session_id: str) -> None:
        """interrupt 失败不掩盖主因（取消/审批 park 已决定 run 走向）。"""
        try:
            self._client.interrupt_session(session_id)
        except OpenCodeError:
            pass

    def _reconcile(self, session_id: str) -> None:
        """用 durable history 补齐事件观测视图（尽力而为，不阻断收尾）。"""
        try:
            events = self._client.history(session_id)
        except OpenCodeError:
            return
        self._mapper.ingest(session_id, events)

    def _raise_park(self, st: dict, ctx: dict, gate: dict) -> None:
        """组装 park 快照并抛 ApprovalInterrupt（由 _drive 捕获后 return）。"""
        st["park"] = {"gate": gate, "ctx": ctx}
        self._persist(st)
        raise ApprovalInterrupt(gate)

    def _accrue_tokens(self, session_id: str) -> None:
        """把会话逐消息 token 求和累进 run 总量（幂等：按会话记已累计值）。

        v2 会话对象的 tokens 聚合恒为 0，用量只能从消息侧取。
        """
        if not hasattr(self, "_accrued"):
            self._accrued: dict[str, int] = {}
        try:
            total = self._client.message_tokens(session_id)
        except OpenCodeError:
            return
        prev = self._accrued.get(session_id, 0)
        if total > prev and self._state is not None:
            self._state["total_tokens"] = self._state.get("total_tokens", 0) + total - prev
            self._accrued[session_id] = total

    # ---------- review ----------

    def _do_review(self, st: dict, frame: dict) -> None:
        outputs = st["worker_outputs"]
        recent = next(reversed(list(outputs)), "")
        text = (
            f"Worker {recent} 完成了步骤，产出：{outputs.get(recent, '')}。请简要点评。"
        )
        answer = self._one_shot(frame, text)
        if self._trace is not None:
            self._trace.emit(self._run_id, "leader_review", frame["agent_name"])
        frame["result"] = answer  # 作为 subteam step 的产出回传父 frame
        if frame["mode"] == "sequential":
            if frame["current"] >= len(frame["plan"]):
                frame["phase"] = "done"
            else:
                frame["stage"] = "gate_step"
        else:
            frame["round"] = {}
            if all(s["status"] in ("done", "skipped") for s in frame["plan"]):
                frame["phase"] = "done"
            else:
                frame["stage"] = None

    # ---------- LLM prompt 辅助 ----------

    def _model_for(self, spec: dict) -> dict[str, str]:
        return translator.model_ref_to_opencode(
            _model_from(spec.get("model")), self._default_model
        )

    def _one_shot(self, spec: dict, text: str) -> str:
        """控制平面一次性 prompt（plan/review）：临时会话 + 异步 prompt + 轮询取文本。

        v2 没有同步 prompt，也没有 per-request system/tools —— 角色设定与
        「不要用工具」的约束由 translator.supervisor_user_prompt 内联进文本；
        这类会话不参与工具白名单执法（与 v1 路径一致：控制面会话无规则集）。
        """
        full = translator.supervisor_user_prompt(spec, text)
        answer = self._ask_once(spec, full)
        if not answer.strip():
            # 实测上游抖动（1.18.32 + 免费模型约 5% 回合）：回合以 finish=stop
            # 正常结束，但只有 reasoning、没有任何 text part。重问必须换全新
            # 会话 —— 同会话里上一段空回合（含其 reasoning）会进上下文，
            # 实测连空两次的概率远高于独立事件。
            answer = self._ask_once(spec, full)
        return answer

    def _ask_once(self, spec: dict, text: str) -> str:
        """新建临时会话跑一轮 prompt，返回本回合文本。"""
        sess = self._client.create_session(
            model=self._model_for(spec), agent=spec["agent_name"]
        )
        session_id = sess["id"]
        self._mapper.register_session(session_id, spec["agent_name"])
        resp = self._client.prompt_async(session_id, text, delivery="queue")
        turn_id = self._client.turn_message_id(resp)
        deadline = time.monotonic() + self._prompt_timeout
        while True:
            if self._rm is not None and self._rm.is_cancelled(self._run_id):
                self._interrupt_quietly(session_id)
                raise RunCancelledError()
            # 与 _wait_sessions 同款双路判定：SSE 的 session.error 之外，
            # REST 兜底 assistant finish=="error"（限流回合形状，见
            # opencode_client.turn_error docstring）。漏掉 REST 路径时，
            # SSE 事件若在订阅建立前发射（剧本线程竞态）就会吊到超时，
            # 且报错信息被替换成"timed out"而非真实 429 原因。
            err = (self._mapper.session_error(session_id)
                   or self._client.turn_error(session_id, turn_id))
            if err:
                raise OpenCodeError(f"opencode prompt failed: {err}")
            if self._client.assistant_done(session_id, turn_id) is not None:
                break
            if time.monotonic() > deadline:
                self._interrupt_quietly(session_id)
                raise OpenCodeError(
                    f"opencode one-shot prompt timed out after "
                    f"{self._prompt_timeout}s: {session_id}"
                )
            time.sleep(self._poll)
        self._accrue_tokens(session_id)
        return self._client.final_text(session_id, turn_id)

    # ---------- 杂项 ----------

    def _find_child(self, frame: dict, name: str) -> dict:
        for c in frame["children"]:
            if c["name"] == name:
                return c
        raise ValueError(
            f"Plan references unknown worker '{name}'; children: "
            f"{[c['name'] for c in frame['children']]}"
        )

    def _check_cancel(self) -> None:
        if self._rm is not None and self._rm.is_cancelled(self._run_id):
            raise RunCancelledError()
