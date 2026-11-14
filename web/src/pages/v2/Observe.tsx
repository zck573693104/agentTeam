/** V2 观察模式:dsh would-accept 账本(主动行为先记录不执行,人工确认转正)。 */
import { useState } from "react";
import { message, Popconfirm } from "antd";
import { useFetch } from "../../hooks/useFetch";
import { v2Post, type V2Evaluation } from "../../api/v2";
import { PageHeader } from "../../components/PageHeader";

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("zh-CN", { hour12: false });
}

function ReviewRow({ ev, onConfirmed }: { ev: V2Evaluation; onConfirmed: () => void }) {
  const [busy, setBusy] = useState(false);

  const confirm = async () => {
    setBusy(true);
    try {
      await v2Post(`/sources/observe/${ev.id}/confirm`, { by: "web" });
      message.success("已转正立项");
      onConfirmed();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="at-panel" style={{ padding: 14 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 6 }}>
        <span className="at-tag" style={{ color: "var(--at-amber)" }}>would-accept</span>
        <span style={{ fontSize: 12, color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)" }}>
          {fmtTime(ev.created_at)}
        </span>
        {ev.ticket_id && (
          <a href={`/v2/tickets/${ev.ticket_id}`} className="at-link" style={{ fontSize: 12 }}>工单 ↗</a>
        )}
      </div>
      <div style={{ fontSize: 13, color: "var(--at-text-dim)", marginBottom: 8 }}>
        {ev.reason || "(无研判理由)"}
      </div>
      {!ev.ticket_id && (
        <Popconfirm title="确认立项?(inferred Mandate)" onConfirm={confirm}>
          <button className="at-btn-amber" disabled={busy}>✓ 确认立项</button>
        </Popconfirm>
      )}
    </div>
  );
}

export default function V2Observe() {
  const { data, loading, refetch } = useFetch<V2Evaluation[]>(
    "/api/sources/observe/reviews",
  );
  return (
    <div>
      <PageHeader
        index="05 · V2 OBSERVE"
        title="观察模式"
        subtitle="Would-Accept Reviews"
        action={<button className="at-btn-amber" onClick={refetch}>⟳ 刷新</button>}
      />
      <div style={{ fontSize: 13, color: "var(--at-text-dim)", marginBottom: 14, fontFamily: "var(--at-font-mono)" }}>
        dsh 安全铁律:一切 inferred/主动行为默认只记录不执行;确认靠谱后再转正立项。
      </div>
      {loading && !data && (
        <div style={{ padding: 40, textAlign: "center", color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 13 }}>
          <span style={{ color: "var(--at-amber)" }}>●</span> LOADING...
        </div>
      )}
      {data && data.length === 0 && (
        <div className="at-panel at-fade-in" style={{ padding: 56, textAlign: "center", color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 13 }}>
          ◌ EMPTY · 暂无待确认的观察记录
        </div>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(420px, 1fr))", gap: 14 }}>
        {(data || []).map((ev) => <ReviewRow key={ev.id} ev={ev} onConfirmed={refetch} />)}
      </div>
    </div>
  );
}
