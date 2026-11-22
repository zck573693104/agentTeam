"""demo 数据种子脚本 — 一键填充控制台演示数据(幂等,可重复执行,无需模型 Key)。

覆盖全部控制台页面:
- 工人池: coder/tester(dev_pool 预设)+ reviewer
- 工单看板: done(repo worktree,含审批故事)/done(普通)/failed/waiting_human
- 注意件收件箱: 待答提问 + 待批技能提案 + 已批审批
- 观察模式: webhook 源(observe)+ 线程/触发器 + would_accept/rejected/accepted 账本
- 记忆库: 复盘教训(全局×2 + 仓库×1)+ 入站研判校准规则
- 工作区: 工单产物文件

用法:
    python scripts/seed_demo.py                       # 默认 data/agentteam_v2.db + data/memory
    python scripts/seed_demo.py --db-path data/demo.db

注意: pending 工单在服务启动后会被真实调度——配置了模型 Key 会实际执行,
未配置则落为 failed(两种结果都符合 demo 语义)。
"""
from __future__ import annotations

import argparse
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentteam.v2.domain import events as ev  # noqa: E402
from agentteam.v2.domain.events import SessionStatus, TicketStatus  # noqa: E402
from agentteam.v2.domain.mandate import Mandate  # noqa: E402
from agentteam.v2.domain.ticket import Ticket  # noqa: E402
from agentteam.v2.domain.worker import WorkerDef  # noqa: E402
from agentteam.v2.kernel.memory import LessonDraft, MemoryStore  # noqa: E402
from agentteam.v2.kernel.workspace import WorkspaceManager  # noqa: E402
from agentteam.v2.storage.attentions import AttentionRepo  # noqa: E402
from agentteam.v2.storage.db import init_db  # noqa: E402
from agentteam.v2.storage.events import EventRepo  # noqa: E402
from agentteam.v2.storage.sources import EvaluationRepo, SourceRepo  # noqa: E402
from agentteam.v2.storage.threads import SubscriptionRepo, ThreadRepo  # noqa: E402
from agentteam.v2.storage.tickets import MessageRepo, SessionRepo, TicketRepo  # noqa: E402
from agentteam.v2.storage.workers import WorkerRepo  # noqa: E402

_NOW = datetime.now(timezone.utc)


def _iso(**kw) -> str:
    return (_NOW - timedelta(**kw)).isoformat()


def _ticket(tid, title, task, status, worker, *, repo=None, result=None,
            ended=None, thread_id=None) -> Ticket:
    return Ticket(
        id=tid, title=title, task=task, status=status, worker_name=worker,
        thread_id=thread_id, repo_path=repo, budget_tokens=0,
        mandate=Mandate(basis="explicit", submitted_by="api",
                        policy_version="demo-pool-v1", scope=None),
        result=result, created_at=_iso(days=3), updated_at=_iso(days=3),
        ended_at=ended,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default="data/agentteam_v2.db")
    parser.add_argument("--memory-dir", default=None)
    args = parser.parse_args()
    db_path = Path(args.db_path)
    memory_dir = Path(args.memory_dir) if args.memory_dir else db_path.parent / "memory"
    workspace_root = db_path.parent / "workspaces"

    conn = init_db(str(db_path))
    lock = threading.Lock()
    workers = WorkerRepo(conn, lock=lock)
    tickets = TicketRepo(conn, lock=lock)
    sessions = SessionRepo(conn, lock=lock)
    messages = MessageRepo(conn, lock=lock)
    events = EventRepo(conn, lock=lock)
    attentions = AttentionRepo(conn, lock=lock)
    threads = ThreadRepo(conn, lock=lock)
    subs = SubscriptionRepo(conn, lock=lock)
    sources = SourceRepo(conn, lock=lock)
    evaluations = EvaluationRepo(conn, lock=lock)
    workspaces = WorkspaceManager(workspace_root)
    memory = MemoryStore(memory_dir)

    # —— 1. 工人池(dev_pool 预设 + reviewer) ——
    from agentteam.v2.presets.dev_pool import WORKERS as DEV_WORKERS
    for w in DEV_WORKERS:
        workers.upsert(w)
    workers.upsert(WorkerDef(
        name="reviewer",
        system_prompt="你是代码评审员,负责 diff review 与清单核查;"
                      "用 read_file/list_dir 检查产出,按评审清单逐项给出结论。",
        tools=["search_web"], max_iterations=8,
    ))

    demo_repo = str((Path(__file__).resolve().parent.parent / "workspaces" / "_demo_repo").resolve())

    # —— 2. 工单 demo_t1: done + git worktree + 审批故事 + 复盘事件 ——
    t1 = "demo_t1_done_worktree"
    if tickets.get(t1) is None:
        tickets.create(_ticket(
            t1, "把 app.txt 更新为 v2,并新增 release.md",
            "把 app.txt 更新为 v2 版本内容,并新增 release.md 记录变更。"
            "验收: app.txt 内容为 v2;release.md 含变更说明。",
            TicketStatus.DONE, "coder", repo=demo_repo,
            result="已完成:app.txt 更新为 v2,新增 release.md 记录变更点。",
            ended=_iso(days=2), thread_id=None,
        ))
        sid = f"demo_s_{t1}"
        ws = workspaces.for_ticket(t1, demo_repo)
        sessions.create(sid, t1, "coder", str(ws), 12,
                        {"provider": "qwen", "name": "qwen-max"})
        sessions.cas_status(sid, SessionStatus.RUNNING, SessionStatus.DONE)
        sessions.add_tokens(sid, 3800, 620)
        messages.append(sid, "system", "你是编码工程师…(demo)")
        messages.append(sid, "user", "把 app.txt 更新为 v2 版本内容,并新增 release.md 记录变更。")
        messages.append(sid, "assistant", "", tool_calls=[
            {"name": "write_file", "args": {"path": "app.txt", "content": "v2"},
             "id": "demo-c1"}])
        messages.append(sid, "tool", "Wrote 2 characters to app.txt", tool_call_id="demo-c1")
        messages.append(sid, "assistant", "", tool_calls=[
            {"name": "write_file", "args": {"path": "release.md",
                                            "content": "# v2\n- app.txt 升级 v2"}, "id": "demo-c2"}])
        messages.append(sid, "tool", "Wrote 17 characters to release.md", tool_call_id="demo-c2")
        messages.append(sid, "assistant", "已完成:app.txt 更新为 v2,新增 release.md 记录变更点。")
        ev_story = [
            (ev.TICKET_CREATED, "api", {"title": "把 app.txt 更新为 v2", "worker": "coder",
                                        "via": "explicit"}),
            (ev.TICKET_DISPATCHED, "system", {"worker": "coder", "note": "目标明确,派 coder"}),
            (ev.TICKET_STATUS_CHANGED, "system", {"to": "running"}),
            (ev.SESSION_STARTED, "coder", {"worker": "coder", "resumed": False}),
            (ev.ATTENTION_CREATED, "coder", {"type": "approval", "tool": "write_file"}),
            (ev.ATTENTION_DECIDED, "boss", {"decision": "approve", "type": "approval"}),
            (ev.TOOL_CALLED, "coder", {"tool": "write_file", "worker": "coder"}),
            (ev.TOOL_RESULT, "coder", {"tool": "write_file", "ok": True,
                                       "result_preview": "Wrote 2 characters to app.txt"}),
            (ev.SESSION_ENDED, "coder", {"status": "done"}),
            (ev.TICKET_STATUS_CHANGED, "system", {"to": "done"}),
            (ev.POSTMORTEM_COMPLETED, "postmortem",
             {"outcome_summary": "顺利交付;变更记录同步是本项目固定动作",
              "lesson_count": 1, "lesson_ids": [], "skill_proposed": None}),
        ]
        for etype, actor, payload in ev_story:
            events.insert(t1, etype, actor=actor, session_id=sid, payload=payload)
        (ws / "release.md").write_text("# v2 变更\n\n- app.txt 内容升级为 v2\n", encoding="utf-8")

    # —— 3. 工单 demo_t2: done(普通目录)+ 复盘事件 ——
    t2 = "demo_t2_done_plain"
    if tickets.get(t2) is None:
        tickets.create(_ticket(
            t2, "写 hello.txt", "在工单工作区写一个 hello.txt,内容: hello agentteam。",
            TicketStatus.DONE, "coder", result="已写入 hello.txt(21 字符)。",
            ended=_iso(days=1),
        ))
        sid = f"demo_s_{t2}"
        ws = workspaces.for_ticket(t2)
        sessions.create(sid, t2, "coder", str(ws), 12,
                        {"provider": "qwen", "name": "qwen-max"})
        sessions.cas_status(sid, SessionStatus.RUNNING, SessionStatus.DONE)
        sessions.add_tokens(sid, 1200, 180)
        messages.append(sid, "system", "你是编码工程师…(demo)")
        messages.append(sid, "user", "在工单工作区写一个 hello.txt,内容: hello agentteam。")
        messages.append(sid, "assistant", "", tool_calls=[
            {"name": "write_file", "args": {"path": "hello.txt", "content": "hello agentteam"},
             "id": "demo-c3"}])
        messages.append(sid, "tool", "Wrote 15 characters to hello.txt", tool_call_id="demo-c3")
        messages.append(sid, "assistant", "已写入 hello.txt。")
        for etype, actor, payload in [
            (ev.TICKET_CREATED, "api", {"title": "写 hello.txt", "worker": "coder",
                                        "via": "explicit"}),
            (ev.TICKET_DISPATCHED, "system", {"worker": "coder", "note": "简单任务"}),
            (ev.TICKET_STATUS_CHANGED, "system", {"to": "running"}),
            (ev.TICKET_STATUS_CHANGED, "system", {"to": "done"}),
            (ev.POSTMORTEM_COMPLETED, "postmortem",
             {"outcome_summary": "琐碎任务,无可提炼教训", "lesson_count": 0,
              "lesson_ids": [], "skill_proposed": None}),
        ]:
            events.insert(t2, etype, actor=actor, session_id=sid, payload=payload)
        (ws / "hello.txt").write_text("hello agentteam", encoding="utf-8")

    # —— 4. 工单 demo_t3: failed(迭代耗尽)+ 失败复盘事件 ——
    t3 = "demo_t3_failed"
    if tickets.get(t3) is None:
        tickets.create(_ticket(
            t3, "修复 CI 构建失败", "修复 CI 构建失败(依赖冲突),验收:构建通过。",
            TicketStatus.FAILED, "coder", result="迭代次数耗尽(12),任务未完成:依赖树冲突超出单工单可解范围。",
            ended=_iso(days=1),
        ))
        sid = f"demo_s_{t3}"
        ws = workspaces.for_ticket(t3)
        sessions.create(sid, t3, "coder", str(ws), 12,
                        {"provider": "qwen", "name": "qwen-max"})
        sessions.cas_status(sid, SessionStatus.RUNNING, SessionStatus.FAILED)
        sessions.add_tokens(sid, 9800, 2400)
        messages.append(sid, "system", "你是编码工程师…(demo)")
        messages.append(sid, "user", "修复 CI 构建失败(依赖冲突)。")
        messages.append(sid, "assistant", "", tool_calls=[
            {"name": "upgrade_deps", "args": {}, "id": "demo-c4"}])
        messages.append(sid, "tool", "错误: 未知工具 'upgrade_deps'", tool_call_id="demo-c4")
        messages.append(sid, "assistant", "工具不可用且迭代耗尽,任务失败。")
        for etype, actor, payload in [
            (ev.TICKET_CREATED, "api", {"title": "修复 CI 构建失败", "worker": "coder",
                                        "via": "explicit"}),
            (ev.TICKET_STATUS_CHANGED, "system", {"to": "running"}),
            (ev.TICKET_STATUS_CHANGED, "system", {"to": "failed",
                                                  "error": "迭代次数耗尽(12)"}),
            (ev.POSTMORTEM_COMPLETED, "postmortem",
             {"outcome_summary": "失败:工具名拼错且未及时查注册表",
              "lesson_count": 1, "lesson_ids": [], "skill_proposed": None}),
        ]:
            events.insert(t3, etype, actor=actor, session_id=sid, payload=payload)

    # —— 5. 工单 demo_t4: waiting_human + 待答提问 ——
    t4 = "demo_t4_waiting"
    if tickets.get(t4) is None:
        tickets.create(_ticket(
            t4, "设计导出功能数据模型", "为报表模块设计数据导出功能的数据模型。",
            TicketStatus.WAITING_HUMAN, "coder",
        ))
        sid = f"demo_s_{t4}"
        ws = workspaces.for_ticket(t4)
        sessions.create(sid, t4, "coder", str(ws), 12,
                        {"provider": "qwen", "name": "qwen-max"})
        sessions.cas_status(sid, SessionStatus.RUNNING, SessionStatus.WAITING_HUMAN)
        messages.append(sid, "system", "你是编码工程师…(demo)")
        messages.append(sid, "user", "为报表模块设计数据导出功能的数据模型。")
        messages.append(sid, "assistant", "已梳理两种方案\nQUESTION: 导出格式优先 Excel 还是 CSV?影响表结构设计")
        att = attentions.create(t4, sid, "question", payload={
            "question": "导出格式优先 Excel 还是 CSV?影响表结构设计",
            "draft_reply": "两种都可支持,建议先定主格式",
            "worker": "coder",
        })
        for etype, actor, payload in [
            (ev.TICKET_CREATED, "api", {"title": "设计导出功能数据模型", "worker": "coder",
                                        "via": "explicit"}),
            (ev.TICKET_STATUS_CHANGED, "system", {"to": "running"}),
            (ev.TICKET_STATUS_CHANGED, "system", {"to": "waiting_human",
                                                  "attention_id": att["id"]}),
        ]:
            events.insert(t4, etype, actor=actor, session_id=sid, payload=payload)

    # —— 6. 工单 demo_t6: done + 待批技能提案(注意件) ——
    t6 = "demo_t6_skill_proposal"
    if tickets.get(t6) is None:
        tickets.create(_ticket(
            t6, "沉淀评审清单技能", "把最近几次代码评审的固定动作沉淀为可复用技能。",
            TicketStatus.DONE, "reviewer", result="已提炼评审固定动作,提案已提交待审批。",
            ended=_iso(hours=10),
        ))
        sid = f"demo_s_{t6}"
        ws = workspaces.for_ticket(t6)
        sessions.create(sid, t6, "reviewer", str(ws), 8,
                        {"provider": "qwen", "name": "qwen-max"})
        sessions.cas_status(sid, SessionStatus.RUNNING, SessionStatus.DONE)
        messages.append(sid, "system", "你是代码评审员…(demo)")
        messages.append(sid, "user", "把最近几次代码评审的固定动作沉淀为可复用技能。")
        messages.append(sid, "assistant", "已提炼评审固定动作,提案提交人工审批。")
        proposal_content = (
            "# 代码评审清单\n\n对 diff 逐项核查,全部通过才给「可合并」结论:\n\n"
            "- [ ] 命名与职责单一(一个函数只做一件事)\n"
            "- [ ] 边界条件与错误处理(空输入/超时/异常路径)\n"
            "- [ ] 测试覆盖关键路径(新增逻辑必须有对应断言)\n"
            "- [ ] 性能敏感点标注(N+1 查询/大循环内 IO)\n"
            "- [ ] 变更说明写入 release.md\n"
        )
        att = attentions.create(t6, sid, "proposal", payload={
            "skill_name": "code_review_checklist",
            "content": proposal_content,
            "summary": "从多次评审任务蒸馏的可复用评审流程",
            "worker": "postmortem",
            "decision_effect": "approve = 写入技能库并热加载;reject = 丢弃提案",
        })
        for etype, actor, payload in [
            (ev.TICKET_CREATED, "api", {"title": "沉淀评审清单技能", "worker": "reviewer",
                                        "via": "explicit"}),
            (ev.TICKET_STATUS_CHANGED, "system", {"to": "running"}),
            (ev.TICKET_STATUS_CHANGED, "system", {"to": "done"}),
            (ev.POSTMORTEM_COMPLETED, "postmortem",
             {"outcome_summary": "评审流程可复用,已提案", "lesson_count": 0,
              "lesson_ids": [], "skill_proposed": "code_review_checklist"}),
            (ev.SKILL_PROPOSED, "postmortem",
             {"skill_name": "code_review_checklist",
              "summary": "从多次评审任务蒸馏的可复用评审流程"}),
            (ev.ATTENTION_CREATED, "postmortem", {"type": "proposal"}),
        ]:
            events.insert(t6, etype, actor=actor, session_id=sid, payload=payload)

    # —— 7. 注意件: 已批审批历史(挂在 t1) ——
    if t1 and not [a for a in attentions.list_by_status("decided", limit=500)
                   if a["ticket_id"] == t1]:
        sid1 = f"demo_s_{t1}"
        a = attentions.create(t1, sid1, "approval", payload={
            "tool": "write_file", "args": {"path": "app.txt", "content": "v2"},
            "worker": "coder", "decision_effect": "approve = 授权延续并执行;reject = 该操作终止",
            "diff": "--- a/app.txt\n+++ b/app.txt\n@@ -1 +1 @@\n-v1\n+v2\n",
        })
        attentions.decide(a["id"], decision="approve", decided_by="boss")
        events.insert(t1, ev.ATTENTION_DECIDED, actor="boss", session_id=sid1,
                      payload={"attention_id": a["id"], "decision": "approve",
                               "type": "approval"})

    # —— 8. 观察模式: webhook 源 + 线程 + 触发器 + 账本 ——
    src = sources.get("demo_group")
    if src is None:
        sources.create("demo_group", "webhook", {"mode": "observe"})
    thread = threads.get_or_create("demo_group", "feishu-chat-1001")
    if not threads.list_triggers(thread["id"]):
        texts = [
            ("alice", "把登录页 500 报错修掉,验收:该路径返回 200"),
            ("bob", "导出接口加个 CSV 格式,验收:能下载"),
            ("carol", "把这次的评审流程沉淀成可复用的评审清单技能"),
            ("dave", "周末谁值班?随便聊聊"),
        ]
        trig_ids = []
        for actor, text in texts:
            trig = threads.append_trigger(thread["id"], actor, text, raw={"demo": True})
            trig_ids.append(trig["id"])
        subs.bind(t6, thread["id"])  # 确认转正的工单与线程绑定
        evaluations.create(trig_ids[0], {"passed": True, "reason": "passed"},
                           decision="would_accept", reason="三事实成立")
        evaluations.create(trig_ids[1], {"passed": True, "reason": "passed"},
                           decision="would_accept", reason="三事实成立")
        evaluations.create(trig_ids[2], {"passed": True, "reason": "passed"},
                           decision="accepted", reason="人工确认转正",
                           ticket_id=t6)
        evaluations.create(trig_ids[3], {"passed": False,
                                         "reason": "命中忽略关键词"},
                           decision="rejected", reason="gate 拒绝: 闲聊无实施意图")

    # —— 9. 记忆库: 教训 + 入站校准规则 ——
    if not memory.list_lessons():
        memory.add_lessons(None, [
            LessonDraft(condition="修改 agentteam/v2/kernel 后回归",
                        lesson="先 python -m pytest tests/v2 -q 跑 v2 子集,再全量,避免整包慢回归"),
            LessonDraft(condition="Windows 下 git worktree add 报分支已存在",
                        lesson="先 git worktree prune 再重挂;仍失败退化普通目录"),
        ])
        memory.add_lessons(demo_repo, [
            LessonDraft(condition="_demo_repo 更新 app.txt 后",
                        lesson="同步新增 release.md 记录版本与变更点,便于 diff review"),
        ])
        memory.record_use(memory.list_lessons()[0]["id"])
    inbound = memory_dir / "inbound"
    if not (inbound / "eval_rules.md").exists():
        inbound.mkdir(parents=True, exist_ok=True)
        (inbound / "eval_rules.md").write_text(
            "# 入站研判校准规则(L0)\n\n源自人工确认样本蒸馏;EvalEngine 注入;可手改。\n\n"
            "- 要求修复具体页面/接口并给出验收标准的请求应接受\n"
            "- 要求沉淀流程/清单类技能的请求应接受\n",
            encoding="utf-8")

    # —— 汇总 ——
    from agentteam.v2.api.deps import create_v2_components
    v2 = create_v2_components(db_path=str(db_path), workspace_root=workspace_root,
                              memory_dir=memory_dir)
    print("✓ demo 数据就绪:")
    print(f"  workers: {[w.name for w in v2.worker_repo.list_all()]}")
    print(f"  tickets: {v2.ticket_repo.count_by_status()}")
    pending = [a['type'] for a in v2.attention_repo.list_by_status('pending')]
    print(f"  pending attentions: {pending}")
    print(f"  observe reviews: {len(v2.evaluation_repo.list_by_decision('would_accept'))} would_accept")
    mem = v2.memory_store.list_lessons() if v2.memory_store else []
    rules_file = memory_dir / "inbound" / "eval_rules.md"
    rule_count = (len([ln for ln in rules_file.read_text(encoding="utf-8").splitlines()
                       if ln.startswith("- ")]) if rules_file.exists() else 0)
    print(f"  memory lessons: {len(mem)}; inbound rules: {rule_count}")
    v2.conn.close()
    conn.close()


if __name__ == "__main__":
    main()
