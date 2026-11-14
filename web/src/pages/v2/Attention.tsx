/**
 * V2 注意件收件箱(v2 默认落地页)。
 *
 * dsh 语义: Attention = 等待人的结构化问题,一次一个;
 * approval → 批准(授权延续并执行)/拒绝(授权终止);
 * question → 回答(注入 transcript 解除阻塞)。
 */
import { useState } from "react";
import { Input, Popconfirm, Tabs, message } from "antd";
import { Link } from "react-router-dom";
import { useFetch } from "../../hooks/useFetch";
import { v2Post, type V2Attention } from "../../api/v2";
import { PageHeader } from "../Runs";

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("zh-CN", { hour12: false });
}

function AttentionCard({
  att,
  onDecided,
}: {
  att: V2Attention;
  onDecided: () => void;
}) {
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);

  const decide = async (decision: string, extra?: Record<string, unknown>) => {
    setBusy(true);
    try {
      await v2Post(`/attentions/${att.id}/decide`, { decision, by: "web", ...extra });
      message.success(`已${decision === "approve" ? "批准" : decision === "reject" ? "拒绝" : "回答"}`);
      onDecided();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const isApproval = att.type === "approval";
  return (
    <div className="at-panel at-panel-corners" style={{ padding: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 10 }}>
        <span className="at-tag" style={{ color: isApproval ? "var(--at-red)" : "var(--at-amber)" }}>
          {isApproval ? "审批 APPROVAL" : "提问 QUESTION"}
        </span>
        <Link to={`/v2/tickets/${att.ticket_id}`} className="at-link" style={{ fontSize: 12 }}>
          工单 {att.ticket_id.slice(0, 8)} ↗
        </Link>
      </div>

      {isApproval ? (
        <>
          <div style={{ fontSize: 14, marginBottom: 6 }}>
            Worker <b style={{ fontFamily: "var(--at-font-mono)" }}>{att.payload.worker}</b>{" "}
            请求执行工具{" "}
            <span className="at-tag" style={{ color: "var(--at-red)" }}>{att.payload.tool}</span>
          </div>
          <pre style={{ fontSize: 13, padding: 10, margin: "0 0 10px", background: "var(--at-bg-deep)",
            border: "1px solid var(--at-border)", whiteSpace: "pre-wrap", maxHeight: 180, overflow: "auto" }}>
            {JSON.stringify(att.payload.args || {}, null, 2)}
          </pre>
          <div style={{ fontSize: 12, color: "var(--at-text-faint)", marginBottom: 10 }}>
            {att.payload.decision_effect || "approve = 授权延续并执行;reject = 该操作终止"}
          </div>
          <div style={{ display: "flex", gap: 10 }}>
            <button className="at-btn-amber" disabled={busy} onClick={() => decide("approve")}>
              ✓ 批准执行
            </button>
            <Popconfirm title="拒绝该操作?worker 可改道" onConfirm={() => decide("reject")}>
              <button className="at-link" style={{ color: "var(--at-red)" }}>✕ 拒绝</button>
            </Popconfirm>
          </div>
        </>
      ) : (
        <>
          <div style={{ fontSize: 14, marginBottom: 6 }}>
            <span style={{ color: "var(--at-amber)" }}>Q:</span>{" "}
            <b>{att.payload.question}</b>
          </div>
          {att.payload.draft_reply && (
            <div style={{ fontSize: 12, color: "var(--at-text-faint)", marginBottom: 10 }}>
              worker 拟回复: {att.payload.draft_reply}
            </div>
          )}
          <div style={{ display: "flex", gap: 8 }}>
            <Input
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              placeholder="输入回答,解除 worker 阻塞"
              onPressEnter={() => answer.trim() && decide("answer", { answer })}
            />
            <button className="at-btn-amber" disabled={busy || !answer.trim()}
              onClick={() => decide("answer", { answer })}>
              回答
            </button>
          </div>
        </>
      )}
      <div style={{ fontSize: 12, color: "var(--at-text-faint)", marginTop: 10 }}>
        等待中 · {fmtTime(att.created_at)}
      </div>
    </div>
  );
}

function DecidedList() {
  const { data } = useFetch<V2Attention[]>("/api/v2/attentions?status=decided");
  if (!data?.length) {
    return <div style={{ color: "var(--at-text-faint)", fontSize: 13, padding: 20, textAlign: "center" }}>
      ◌ 暂无已决定记录
    </div>;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {data.map((a) => (
        <div key={a.id} className="at-panel" style={{ padding: "10px 14px", display: "flex", gap: 12, alignItems: "center" }}>
          <span className="at-tag" style={{ color: a.decision === "approve" ? "var(--at-green)"
            : a.decision === "reject" || a.decision === "revoked" ? "var(--at-red)" : "var(--at-amber)" }}>
            {a.decision}
          </span>
          <span style={{ fontSize: 13, color: "var(--at-text-dim)" }}>
            {a.type === "approval" ? `审批 ${a.payload.tool}` : `提问 ${a.payload.question}`}
          </span>
          <Link to={`/v2/tickets/${a.ticket_id}`} className="at-link" style={{ fontSize: 12, marginLeft: "auto" }}>
            ↗
          </Link>
        </div>
      ))}
    </div>
  );
}

export default function V2Attention() {
  const { data, loading, refetch } = useFetch<V2Attention[]>("/api/v2/attentions?status=pending");

  return (
    <div>
      <PageHeader
        index="05 · V2 ATTENTION"
        title="注意件收件箱"
        subtitle="Human Inbox"
        action={
          <button className="at-btn-amber" onClick={refetch}>⟳ 刷新</button>
        }
      />
      {loading && !data && (
        <div style={{ padding: 40, textAlign: "center", color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 13 }}>
          <span style={{ color: "var(--at-amber)" }}>●</span> LOADING...
        </div>
      )}
      {data && data.length === 0 && (
        <div className="at-panel at-fade-in" style={{ padding: 56, textAlign: "center", color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 13 }}>
          ✓ INBOX CLEAR · 无待处理注意件
        </div>
      )}
      <Tabs
        items={[
          {
            key: "pending",
            label: `待处理 (${data?.length ?? 0})`,
            children: (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(420px, 1fr))", gap: 14 }}>
                {(data || []).map((a) => (
                  <AttentionCard key={a.id} att={a} onDecided={refetch} />
                ))}
              </div>
            ),
          },
          { key: "decided", label: "已决定", children: <DecidedList /> },
        ]}
      />
    </div>
  );
}
