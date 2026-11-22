/**
 * V2 工单详情:B6 typed timeline + B7 diff review + transcript + 追问直通 + 撤权。
 *
 * Timeline(Langfuse/Devin 模式): 事件按类型分组着色,工具对(tool_called→tool_result)
 * 合并为可展开条目;attention/预算高亮;SSE 全集渲染。
 */
import { useEffect, useRef, useState } from "react";
import { Input, Popconfirm, message } from "antd";
import { Link, useParams } from "react-router-dom";
import { useFetch } from "../../hooks/useFetch";
import {
  v2Post,
  type V2ChangeSet, type V2Event, type V2Message, type V2Session, type V2Ticket,
} from "../../api/v2";
import { PageHeader } from "../../components/PageHeader";
import MiniMarkdown from "../../components/MiniMarkdown";

const STATUS_COLOR: Record<string, string> = {
  pending: "var(--at-gray)", running: "var(--at-green)",
  waiting_human: "var(--at-amber)", done: "var(--at-gold)",
  failed: "var(--at-red)", cancelled: "var(--at-text-faint)",
};

// B6: 事件类型 → 类别(着色与分组)
function eventCategory(type: string): "lifecycle" | "tool" | "attention" | "human" | "session" | "other" {
  if (type.startsWith("ticket_")) return "lifecycle";
  if (type.startsWith("tool_")) return "tool";
  if (type.startsWith("attention_")) return "attention";
  if (type === "follow_up_delivered") return "human";
  if (type.startsWith("session_")) return "session";
  return "other";
}
const CAT_COLOR: Record<string, string> = {
  lifecycle: "var(--at-gold)", tool: "var(--at-cyan)", attention: "var(--at-amber)",
  human: "var(--at-green)", session: "var(--at-text-dim)", other: "var(--at-text-faint)",
};

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("zh-CN", { hour12: false });
}

/** 工具对合并:tool_called + 最近的 tool_result 合成一个可展开条目。 */
interface EventEntry {
  events: V2Event[];
  kind: "tool" | "single";
}

function buildEntries(events: V2Event[]): EventEntry[] {
  const entries: EventEntry[] = [];
  let pending: V2Event | null = null;
  for (const e of events) {
    if (e.type === "tool_called") {
      if (pending) entries.push({ events: [pending], kind: "single" });
      pending = e;
    } else if (e.type === "tool_result" && pending) {
      entries.push({ events: [pending, e], kind: "tool" });
      pending = null;
    } else {
      if (pending) entries.push({ events: [pending], kind: "single" });
      pending = null;
      entries.push({ events: [e], kind: "single" });
    }
  }
  if (pending) entries.push({ events: [pending], kind: "single" });
  return entries;
}

function EventEntryRow({ entry }: { entry: EventEntry }) {
  const [open, setOpen] = useState(false);
  const first = entry.events[0];
  const cat = eventCategory(first.type);
  const color = CAT_COLOR[cat];
  const result = entry.events[1];

  if (entry.kind === "tool") {
    const ok = result.payload.ok !== false;
    const approved = !!result.payload.approved;
    return (
      <div style={{ borderBottom: "1px solid var(--at-border-soft)", fontSize: 12, fontFamily: "var(--at-font-mono)" }}>
        <div onClick={() => setOpen(!open)} style={{ display: "flex", gap: 10, padding: "5px 0", cursor: "pointer", alignItems: "baseline" }}>
          <span style={{ color: "var(--at-text-faint)", minWidth: 64 }}>{fmtTime(first.created_at).slice(11)}</span>
          <span style={{ color, minWidth: 120 }}>🔧 {first.payload.tool}</span>
          <span style={{ color: approved ? "var(--at-amber)" : ok ? "var(--at-green)" : "var(--at-red)" }}>
            {approved ? "已批准" : ok ? "ok" : "err"}
          </span>
          <span style={{ color: "var(--at-text-faint)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {result.payload.result_preview || ""}
          </span>
          <span style={{ color: "var(--at-text-faint)" }}>{open ? "▾" : "▸"}</span>
        </div>
        {open && (
          <div style={{ padding: "0 0 8px 64px", color: "var(--at-text-dim)" }}>
            <div style={{ color: "var(--at-text-faint)" }}>args:</div>
            <pre style={{ margin: "4px 0", padding: 8, background: "var(--at-bg-deep)", fontSize: 11, overflow: "auto", maxHeight: 150 }}>
              {JSON.stringify(first.payload.args ?? first.payload, null, 2)}
            </pre>
            <div style={{ color: "var(--at-text-faint)" }}>result:</div>
            <pre style={{ margin: "4px 0", padding: 8, background: "var(--at-bg-deep)", fontSize: 11, overflow: "auto", maxHeight: 150, whiteSpace: "pre-wrap" }}>
              {result.payload.result_preview}
            </pre>
          </div>
        )}
      </div>
    );
  }

  return (
    <div style={{ display: "flex", gap: 10, padding: "5px 0", borderBottom: "1px solid var(--at-border-soft)",
      fontFamily: "var(--at-font-mono)", fontSize: 12, alignItems: "baseline" }}>
      <span style={{ color: "var(--at-text-faint)", minWidth: 64 }}>{fmtTime(first.created_at).slice(11)}</span>
      <span style={{ color, minWidth: 130 }}>{first.type}</span>
      <span style={{ color: "var(--at-text-dim)", minWidth: 70 }}>{first.actor}</span>
      <span style={{ color: "var(--at-text-dim)", flex: 1, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
        {first.type === "ticket_created" && first.payload.task ? String(first.payload.task).slice(0, 120)
          : first.type === "attention_created" ? `需要人工: ${first.payload.type} ${first.payload.question || first.payload.tool || ""}`
            : first.type === "session_ended" ? (first.payload.status || "")
              : JSON.stringify(first.payload).slice(0, 180)}
      </span>
    </div>
  );
}

/** B7: unified diff 渲染(+绿/-红/@@ 灰)。 */
function DiffView({ patch, maxHeight = 300 }: { patch: string; maxHeight?: number }) {
  const lines = patch.split("\n");
  return (
    <pre style={{ margin: 0, padding: 10, background: "var(--at-bg-deep)", border: "1px solid var(--at-border)",
      fontSize: 11, fontFamily: "var(--at-font-mono)", lineHeight: 1.5, overflow: "auto", maxHeight }}>
      {lines.map((line: string, i: number) => (
        <div key={i} style={{
          color: line.startsWith("+") && !line.startsWith("+++") ? "var(--at-green)"
            : line.startsWith("-") && !line.startsWith("---") ? "var(--at-red)"
              : line.startsWith("@@") ? "var(--at-cyan)" : "var(--at-text-dim)",
          whiteSpace: "pre-wrap", wordBreak: "break-all",
        }}>{line}</div>
      ))}
    </pre>
  );
}

function Transcript({ ticketId, sessionId }: { ticketId: string; sessionId: string }) {
  const { data } = useFetch<V2Message[]>(
    sessionId ? `/api/tickets/${ticketId}/messages?session_id=${sessionId}` : null,
  );
  if (!data?.length) return <div style={{ color: "var(--at-text-faint)", fontSize: 12 }}>(空)</div>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {data.map((m) => (
        <div key={m.id} style={{ fontSize: 12, fontFamily: "var(--at-font-mono)" }}>
          <span style={{
            color: m.role === "assistant" ? "var(--at-amber)"
              : m.role === "user" ? "var(--at-cyan)"
                : m.role === "tool" ? "var(--at-text-faint)" : "var(--at-gray)",
          }}>[{m.role}]</span>{" "}
          <span style={{ color: "var(--at-text-dim)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
            {m.content?.slice(0, 600) || (m.tool_calls ? `调用工具: ${m.tool_calls.map((c) => c.name).join(", ")}` : "")}
          </span>
        </div>
      ))}
    </div>
  );
}

export default function V2TicketDetail() {
  const { ticketId = "" } = useParams();
  const { data: ticket, refetch } = useFetch<V2Ticket>(`/api/tickets/${ticketId}`);
  const { data: events } = useFetch<V2Event[]>(`/api/tickets/${ticketId}/events`);
  const [liveEvents, setLiveEvents] = useState<V2Event[]>([]);
  const [followUp, setFollowUp] = useState("");
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!ticketId) return;
    const es = new EventSource(`/api/tickets/${ticketId}/stream`);
    esRef.current = es;
    es.onmessage = (ev) => {
      try {
        const e = JSON.parse(ev.data) as V2Event;
        setLiveEvents((prev) => {
          if (prev.some((x) => x.id === e.id)) return prev;
          return [...prev, e];
        });
      } catch { /* 忽略坏帧 */ }
    };
    es.onerror = () => { /* EventSource 自动重连 */ };
    return () => { es.close(); esRef.current = null; };
  }, [ticketId]);

  const allEvents = (() => {
    const seen = new Set<number>();
    const merged: V2Event[] = [];
    for (const e of [...(events || []), ...liveEvents]) {
      if (!seen.has(e.id)) { seen.add(e.id); merged.push(e); }
    }
    return merged.sort((a, b) => a.id - b.id);
  })();

  const sendFollowUp = async () => {
    if (!followUp.trim()) return;
    try {
      await v2Post(`/tickets/${ticketId}/messages`, { text: followUp, by: "web" });
      message.success("追问已送达 worker(只入不回)");
      setFollowUp("");
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const cancelTicket = async () => {
    try {
      await v2Post(`/tickets/${ticketId}/cancel`, { by: "web" });
      message.success("已撤权(revoked_by_human)");
      refetch();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  if (!ticket) {
    return <div style={{ padding: 40, color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 13 }}>LOADING...</div>;
  }

  const sessions: V2Session[] = ticket.sessions || [];
  const lastSession = sessions[sessions.length - 1];
  const terminal = ["done", "failed", "cancelled"].includes(ticket.status);
  const usedTokens = sessions.reduce((sum, s) => sum + (s.tokens_in || 0) + (s.tokens_out || 0), 0);

  return (
    <div>
      <PageHeader
        index="03 · V2 TICKET"
        title={ticket.title || ticket.id.slice(0, 8)}
        subtitle="Ticket Detail"
        action={
          !terminal ? (
            <Popconfirm title="撤权?运行中工单将被中断(dsh: 人的停止 = 撤权)" onConfirm={cancelTicket}>
              <button className="at-link" style={{ color: "var(--at-red)" }}>✕ 撤权</button>
            </Popconfirm>
          ) : null
        }
      />

      {/* 工单元信息 */}
      <div className="at-panel at-panel-corners at-fade-in" style={{ padding: 16, marginBottom: 14 }}>
        <div style={{ display: "flex", gap: 20, flexWrap: "wrap", alignItems: "baseline" }}>
          <span className="at-tag" style={{ color: STATUS_COLOR[ticket.status] }}>{ticket.status}</span>
          <span style={{ fontSize: 13, fontFamily: "var(--at-font-mono)" }}>
            worker: <b style={{ color: "var(--at-green)" }}>{ticket.worker_name || "—"}</b>
          </span>
          <span style={{ fontSize: 13, fontFamily: "var(--at-font-mono)", color: "var(--at-text-dim)" }}>
            预算: <span style={{ color: usedTokens > 0 ? "var(--at-cyan)" : "var(--at-text-faint)" }}>
              {usedTokens.toLocaleString()}{ticket.budget_tokens ? ` / ${ticket.budget_tokens.toLocaleString()}` : " tokens(不限额)"}
            </span>
          </span>
          {ticket.mandate && (
            <span style={{ fontSize: 13, fontFamily: "var(--at-font-mono)", color: "var(--at-text-dim)" }}>
              授权: <span style={{ color: ticket.mandate.basis === "inferred" ? "var(--at-blue)" : "var(--at-amber)" }}>
                {ticket.mandate.basis}
              </span> · {ticket.mandate.submitted_by} · policy {ticket.mandate.policy_version.slice(0, 8)}
            </span>
          )}
          <span style={{ fontSize: 12, color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", marginLeft: "auto" }}>
            {fmtTime(ticket.created_at)} → {fmtTime(ticket.ended_at)}
          </span>
        </div>
        <div style={{ fontSize: 14, marginTop: 10, lineHeight: 1.6, color: "var(--at-text)" }}>
          {ticket.task}
        </div>
        {ticket.result && (
          ticket.result.trimStart().startsWith("#") ? (
            /* 报告类结果(晨报/复盘 markdown) → 排版渲染 */
            <div style={{
              marginTop: 10, padding: "14px 16px", background: "var(--at-bg-panel)",
              border: "1px solid var(--at-border)", borderRadius: 4,
            }}>
              <div className="at-stat-label" style={{ marginBottom: 8, display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ color: "var(--at-amber)" }}>▤</span> REPORT
              </div>
              <MiniMarkdown source={ticket.result} />
            </div>
          ) : (
            <div style={{
              fontSize: 13, marginTop: 10, padding: 10, background: "var(--at-bg-deep)",
              border: "1px solid var(--at-border)", whiteSpace: "pre-wrap", color: "var(--at-green)",
            }}>
              <b>结果:</b> {ticket.result}
            </div>
          )
        )}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, alignItems: "start" }}>
        {/* 左列: 会话/transcript/变更集 */}
        <div>
          <div className="at-panel" style={{ padding: 14, marginBottom: 14 }}>
            <div className="at-stat-label">Sessions · {sessions.length}</div>
            {sessions.map((s) => (
              <div key={s.id} style={{ fontSize: 12, fontFamily: "var(--at-font-mono)", color: "var(--at-text-dim)", marginBottom: 4 }}>
                {s.worker_name} · {s.status} · {s.iterations}/{s.max_iterations} 迭代 ·
                tokens {(s.tokens_in || 0).toLocaleString()}/{(s.tokens_out || 0).toLocaleString()}
              </div>
            ))}
            <div style={{ borderTop: "1px solid var(--at-border-soft)", marginTop: 8, paddingTop: 10 }}>
              <div className="at-stat-label" style={{ marginBottom: 8 }}>Transcript</div>
              {lastSession ? <Transcript ticketId={ticketId} sessionId={lastSession.id} /> : <div style={{ fontSize: 12, color: "var(--at-text-faint)" }}>(尚未开始)</div>}
            </div>
            {!terminal && (
              <div style={{ borderTop: "1px solid var(--at-border-soft)", marginTop: 10, paddingTop: 10, display: "flex", gap: 8 }}>
                <Input
                  value={followUp}
                  onChange={(e) => setFollowUp(e.target.value)}
                  onPressEnter={sendFollowUp}
                  placeholder="追问直达 worker(只入不回,不过 Router)"
                  size="small"
                />
                <button className="at-btn-amber" onClick={sendFollowUp} style={{ padding: "4px 10px" }}>送达</button>
              </div>
            )}
          </div>

          {/* B7 变更集(git worktree 工单) */}
          {ticket.repo_path && <ChangeSetPanel ticketId={ticketId} />}
        </div>

        {/* 右列: B6 typed timeline */}
        <div className="at-panel" style={{ padding: 14 }}>
          <div className="at-stat-label" style={{ display: "flex", justifyContent: "space-between" }}>
            <span>Timeline · {allEvents.length}{!terminal && " · LIVE ●"}</span>
            <Link to="/v2/tickets" className="at-link" style={{ fontSize: 12 }}>← 看板</Link>
          </div>
          <div style={{ maxHeight: 560, overflow: "auto" }}>
            {buildEntries(allEvents).map((entry, i) => (
              <EventEntryRow key={i} entry={entry} />
            ))}
            {allEvents.length === 0 && (
              <div style={{ color: "var(--at-text-faint)", fontSize: 12, textAlign: "center", padding: 16 }}>(暂无事件)</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/** B7 变更集面板: stat + patch(可折叠)。 */
function ChangeSetPanel({ ticketId }: { ticketId: string }) {
  const { data, loading } = useFetch<V2ChangeSet>(
    `/api/tickets/${ticketId}/diff`,
  );
  const [open, setOpen] = useState(false);
  if (loading && !data) {
    return <div className="at-panel" style={{ padding: 14, fontSize: 12, color: "var(--at-text-faint)" }}>变更集加载中…</div>;
  }
  if (!data?.is_git) {
    return <div className="at-panel" style={{ padding: 14, fontSize: 12, color: "var(--at-text-faint)" }}>
      {data?.note || "非 git 工作区"}
    </div>;
  }
  return (
    <div className="at-panel" style={{ padding: 14 }}>
      <div className="at-stat-label" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span>变更集(vs {data.base})</span>
        <button className="at-link" style={{ fontSize: 12 }} onClick={() => setOpen(!open)}>
          {open ? "▾ 收起" : "▸ 展开 patch"}
        </button>
      </div>
      {data.stat && (
        <pre style={{ fontSize: 12, fontFamily: "var(--at-font-mono)", color: "var(--at-cyan)", margin: "8px 0", whiteSpace: "pre-wrap" }}>
          {data.stat}
        </pre>
      )}
      {!data.stat && <div style={{ fontSize: 12, color: "var(--at-text-faint)" }}>(无变更)</div>}
      {open && data.patch && <DiffView patch={data.patch} maxHeight={420} />}
    </div>
  );
}
