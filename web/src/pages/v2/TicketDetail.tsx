/**
 * V2 工单详情:Mandate / 会话 / transcript / 事件流(SSE 全集渲染) / 追问直通 / 撤权。
 * SSE 无事件白名单——后端新事件类型无需改前端(修复 v1 痛点)。
 */
import { useEffect, useRef, useState } from "react";
import { Input, Popconfirm, message } from "antd";
import { Link, useParams } from "react-router-dom";
import { useFetch } from "../../hooks/useFetch";
import { v2Post, type V2Event, type V2Message, type V2Session, type V2Ticket } from "../../api/v2";
import { PageHeader } from "../../components/PageHeader";

const STATUS_COLOR: Record<string, string> = {
  pending: "var(--at-gray)", running: "var(--at-green)",
  waiting_human: "var(--at-amber)", done: "var(--at-gold)",
  failed: "var(--at-red)", cancelled: "var(--at-text-faint)",
};

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("zh-CN", { hour12: false });
}

function EventRow({ e }: { e: V2Event }) {
  const color =
    e.type.includes("cancelled") ? "var(--at-red)"
      : e.type.includes("attention") ? "var(--at-amber)"
        : e.type.includes("tool") ? "var(--at-cyan)"
          : e.type === "session_ended" || e.type === "ticket_status_changed" ? "var(--at-gold)"
            : "var(--at-text-faint)";
  return (
    <div style={{ display: "flex", gap: 10, padding: "4px 0", borderBottom: "1px solid var(--at-border-soft)",
      fontFamily: "var(--at-font-mono)", fontSize: 12, alignItems: "baseline" }}>
      <span style={{ color: "var(--at-text-faint)", minWidth: 66 }}>{fmtTime(e.created_at).slice(11)}</span>
      <span style={{ color, minWidth: 170 }}>{e.type}</span>
      <span style={{ color: "var(--at-text-dim)", minWidth: 70 }}>{e.actor}</span>
      <span style={{ color: "var(--at-text-dim)", flex: 1, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
        {JSON.stringify(e.payload).slice(0, 220)}
      </span>
    </div>
  );
}

function Transcript({ ticketId, sessionId }: { ticketId: string; sessionId: string }) {
  const { data } = useFetch<V2Message[]>(
    sessionId ? `/api/v2/tickets/${ticketId}/messages?session_id=${sessionId}` : null,
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
  const { data: ticket, refetch } = useFetch<V2Ticket>(`/api/v2/tickets/${ticketId}`);
  const { data: events } = useFetch<V2Event[]>(`/api/v2/tickets/${ticketId}/events`);
  const [liveEvents, setLiveEvents] = useState<V2Event[]>([]);
  const [followUp, setFollowUp] = useState("");
  const esRef = useRef<EventSource | null>(null);

  // SSE 全集渲染:订阅先于回放 + Last-Event-ID 游标(后端已处理)
  useEffect(() => {
    if (!ticketId) return;
    const es = new EventSource(`/api/v2/tickets/${ticketId}/stream`);
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

  // 终态后不再收新事件,SSE 静默 → 合并 REST 兜底
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

  return (
    <div>
      <PageHeader
        index="02 · V2 TICKET"
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
          <div style={{
            fontSize: 13, marginTop: 10, padding: 10, background: "var(--at-bg-deep)",
            border: "1px solid var(--at-border)", whiteSpace: "pre-wrap", color: "var(--at-green)",
          }}>
            <b>结果:</b> {ticket.result}
          </div>
        )}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, alignItems: "start" }}>
        {/* 会话 + transcript */}
        <div className="at-panel" style={{ padding: 14 }}>
          <div className="at-stat-label">Sessions · {sessions.length}</div>
          {sessions.map((s) => (
            <div key={s.id} style={{ fontSize: 12, fontFamily: "var(--at-font-mono)", color: "var(--at-text-dim)", marginBottom: 4 }}>
              {s.worker_name} · {s.status} · {s.iterations}/{s.max_iterations} 迭代
            </div>
          ))}
          <div style={{ borderTop: "1px solid var(--at-border-soft)", marginTop: 8, paddingTop: 10 }}>
            <div className="at-stat-label" style={{ marginBottom: 8 }}>Transcript</div>
            {lastSession ? <Transcript ticketId={ticketId} sessionId={lastSession.id} /> : <div style={{ fontSize: 12, color: "var(--at-text-faint)" }}>(尚未开始)</div>}
          </div>
          {/* 追问直通 */}
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

        {/* 事件流(SSE 全集) */}
        <div className="at-panel" style={{ padding: 14 }}>
          <div className="at-stat-label" style={{ display: "flex", justifyContent: "space-between" }}>
            <span>Events · {allEvents.length}{!terminal && " · LIVE ●"}</span>
            <Link to="/v2/tickets" className="at-link" style={{ fontSize: 12 }}>← 看板</Link>
          </div>
          <div style={{ maxHeight: 520, overflow: "auto" }}>
            {allEvents.map((e) => <EventRow key={e.id} e={e} />)}
            {allEvents.length === 0 && (
              <div style={{ color: "var(--at-text-faint)", fontSize: 12, textAlign: "center", padding: 16 }}>(暂无事件)</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
