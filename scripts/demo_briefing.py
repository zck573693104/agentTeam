"""模拟部门晨报生成(P7 demo):按部门逐人生成报告。

数据来源:
- 工单统计: data/agentteam_v2.db 真实数据(TicketRepo.daily_stats)
- 禅道/考勤/代码: 渠道未接入,用确定性模拟数据(按人名哈希稳定生成,标注「模拟」)

产出:
1. 终端打印两份部门报告
2. 按部门落为晨报工单(done,worker=部门领导),控制台工单详情/组织过滤可见
幂等: 固定工单 id(重复执行不重复立项)。
"""
from __future__ import annotations

import hashlib
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentteam.storage.utils import utcnow_iso as _now  # noqa: E402
from agentteam.v2.domain import events as ev  # noqa: E402
from agentteam.v2.domain.events import SessionStatus, TicketStatus  # noqa: E402
from agentteam.v2.domain.mandate import Mandate  # noqa: E402
from agentteam.v2.domain.ticket import Ticket  # noqa: E402
from agentteam.v2.kernel.workspace import WorkspaceManager  # noqa: E402
from agentteam.v2.storage.db import init_db  # noqa: E402
from agentteam.v2.storage.events import EventRepo  # noqa: E402
from agentteam.v2.storage.org import OrgRepo  # noqa: E402
from agentteam.v2.storage.tickets import MessageRepo, SessionRepo, TicketRepo  # noqa: E402
from agentteam.v2.storage.workers import WorkerRepo  # noqa: E402

TODAY = datetime.now().strftime("%Y-%m-%d")


def _stable(name: str, salt: str, lo: int, hi: int) -> int:
    """按人名确定性生成模拟数(重复执行结果一致)。"""
    h = int(hashlib.md5(f"{name}:{salt}".encode("utf-8")).hexdigest()[:8], 16)
    return lo + h % (hi - lo + 1)


def _mock_channels(name: str) -> tuple[str, list[str]]:
    """禅道/代码/考勤模拟(渠道未接入;接入后由 webhook/MCP 真实数据替换)。

    返回 (展示文本, 风险项列表)。"""
    zen_done = _stable(name, "zen_done", 0, 3)
    zen_doing = _stable(name, "zen_doing", 1, 4)
    zen_block = _stable(name, "zen_block", 0, 1)
    commits = _stable(name, "commits", 0, 6)
    prs = _stable(name, "prs", 0, 2)
    late = _stable(name, "late", 0, 1)
    zen_str = f"已完成 {zen_done} / 进行中 {zen_doing}"
    if zen_block:
        zen_str += f" / **阻塞 {zen_block}**(模拟)"
    att = "正常" if not late else f"迟到 {late} 次(模拟)"
    text = (
        f"- 禅道任务: {zen_str}\n"
        f"- 代码: 昨日提交 {commits} 次,待审 PR {prs} 个(模拟)\n"
        f"- 考勤: {att}"
    )
    risks: list[str] = []
    if zen_block:
        risks.append(f"{name} 禅道任务阻塞 {zen_block} 项,建议今日协调资源")
    if late:
        risks.append(f"{name} 昨日考勤迟到 {late} 次,请关注")
    return text, risks


def _dept_report(dept: str, members: list[tuple[str, str]], stats: dict,
                 lead: str | None, channels: dict[str, tuple[str, list[str]]]) -> str:
    """单个部门的晨报(markdown)。members: (worker_name, 组名) 已去重。"""
    lines = [f"# {dept} 晨报 · {TODAY}", ""]
    y_done = sum(s.get("done", 0) for n, _ in members for s in [stats.get(n, {})])
    y_fail = sum(s.get("failed", 0) for n, _ in members for s in [stats.get(n, {})])
    y_cancel = sum(s.get("cancelled", 0) for n, _ in members for s in [stats.get(n, {})])
    active = sum(s.get("pending", 0) + s.get("running", 0) + s.get("waiting_human", 0)
                 for n, _ in members for s in [stats.get(n, {})])
    lines += [
        "## 总览",
        f"- 近 24 小时工单: 完成 **{y_done}** / 失败 **{y_fail}** / 取消 {y_cancel};当前在途 **{active}**",
        f"- 部门负责人: {lead or '—'}(本报告由 cron 晨报流程生成,外部渠道数据为模拟)",
        "",
        "## 逐人情况",
    ]
    risks: list[str] = []
    current_group: str | None = None
    for name, group in members:
        if group != current_group:
            lines.append(f"### {group or '部门直属'}")
            current_group = group
        s = stats.get(name, {})
        failed = s.get("failed", 0)
        doing = s.get("pending", 0) + s.get("running", 0)
        waiting = s.get("waiting_human", 0)
        line = f"- **{name}**: 近24h完成 {s.get('done', 0)} / 失败 {failed};在途 {doing}"
        if waiting:
            line += f";等待人工 {waiting}(注意件待处理)"
        lines.append(line)
        channel_text, channel_risks = channels.get(name, ("- 外部渠道无数据", []))
        lines.append(channel_text)
        if failed:
            risks.append(f"{name} 近24h {failed} 单失败,建议晨会跟进失败原因")
        if waiting:
            risks.append(f"{name} 有工单等待人工决策,请及时处理注意件")
        risks.extend(channel_risks)
    lines += ["", "## 风险与建议"]
    if risks:
        lines += [f"- {r}" for r in risks]
    else:
        lines.append("- 无异常,按计划推进")
    lines.append("- 数据口径: 工单=AgentTeam 真实统计;禅道/代码/考勤=模拟(渠道未接入)")
    return "\n".join(lines)


def main() -> None:
    db_path = Path("data/agentteam_v2.db")
    conn = init_db(str(db_path))
    lock = threading.Lock()
    tickets = TicketRepo(conn, lock=lock)
    sessions = SessionRepo(conn, lock=lock)
    messages = MessageRepo(conn, lock=lock)
    events = EventRepo(conn, lock=lock)
    workers = WorkerRepo(conn, lock=lock)
    org = OrgRepo(conn, lock=lock)
    workspaces = WorkspaceManager(db_path.parent / "workspaces")

    # 近 24 小时窗口(本地时区)
    local_now = datetime.now().astimezone()
    y0 = local_now - timedelta(hours=24)
    stats = tickets.daily_stats(y0.astimezone(timezone.utc).isoformat(),
                                local_now.astimezone(timezone.utc).isoformat())

    all_workers = workers.list_all()
    reports: list[tuple[str, str, str]] = []  # (dept_id, dept_name, report)

    for dept in org.tree():
        lead = next((w.name for w in all_workers if w.org_node_id == dept["id"]), None)
        seen: set[str] = set()
        members: list[tuple[str, str]] = []

        def walk(node: dict, group_name: str | None) -> None:
            for w in all_workers:
                if w.org_node_id == node["id"] and w.name != lead and w.name not in seen:
                    seen.add(w.name)
                    members.append((w.name, group_name or "部门直属"))
            for c in node.get("children") or []:
                walk(c, c["name"])

        walk(dept, "部门直属")
        if not members and not lead:
            continue
        channels = {n: _mock_channels(n) for n, _ in members}
        report = _dept_report(dept["name"], members, stats, lead, channels)
        reports.append((dept["id"], dept["name"], report))

        tid = f"demo_brief_{dept['id']}_{TODAY.replace('-', '')}"
        existing = tickets.get(tid)
        if existing is not None and existing.result != report:
            # 报告口径/数据更新 → 重写结果(工单壳复用,保持幂等)
            conn.execute(
                "UPDATE tickets SET result = ?, updated_at = ? WHERE id = ?",
                (report, _now(), tid),
            )
            conn.commit()
            print(f"✓ 已更新晨报工单: {tid[:30]}")
        if existing is None:
            tickets.create(Ticket(
                id=tid, title=f"{dept['name']}晨报 {TODAY}",
                task=f"生成 {dept['name']} {TODAY} 晨报:按下属逐人汇总昨日工单/禅道/代码/考勤。",
                status=TicketStatus.DONE, worker_name=lead,
                thread_id=None, repo_path=None, budget_tokens=0,
                mandate=Mandate(basis="explicit", submitted_by="source:briefing",
                                policy_version="demo-pool-v1", scope=None),
                result=report, created_at=_now(), updated_at=_now(), ended_at=_now(),
            ))
            sid = f"demo_s_{tid}"
            ws = workspaces.for_ticket(tid)
            sessions.create(sid, tid, lead or "reviewer", str(ws), 6,
                            {"provider": "qwen", "name": "qwen-max"})
            sessions.cas_status(sid, SessionStatus.RUNNING, SessionStatus.DONE)
            messages.append(sid, "system", "你是部门领导…(晨报 demo)")
            messages.append(sid, "user", f"生成 {dept['name']} 今日晨报。")
            messages.append(sid, "assistant", report)
            for etype, payload in [
                (ev.TICKET_CREATED, {"title": f"{dept['name']}晨报", "worker": lead,
                                     "via": "explicit"}),
                (ev.TICKET_DISPATCHED, {"worker": lead, "note": "cron 晨报(模拟数据版)"}),
                (ev.TICKET_STATUS_CHANGED, {"to": "running"}),
                (ev.TICKET_STATUS_CHANGED, {"to": "done"}),
            ]:
                events.insert(tid, etype, actor="postmortem", session_id=sid, payload=payload)

    for _, dept_name, report in reports:
        print(report)
        print("\n" + "=" * 60 + "\n")
    print(f"✓ {len(reports)} 份部门晨报已生成,工单 id: demo_brief_<部门>_{TODAY.replace('-', '')}")
    conn.close()


if __name__ == "__main__":
    main()
