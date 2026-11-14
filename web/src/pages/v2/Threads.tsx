/** V2 线程:入站事件时间序(dsh: Thread = Trigger 时间序,Eval 的上下文单元)。 */
import { useState } from "react";
import { Input, message } from "antd";
import { useFetch } from "../../hooks/useFetch";
import { v2Post, type V2Thread } from "../../api/v2";
import { PageHeader } from "../../components/PageHeader";

export default function V2Threads() {
  const { data: threads, refetch } = useFetch<V2Thread[]>("/api/v2/threads");
  const [selected, setSelected] = useState<string | null>(null);
  const [form, setForm] = useState({ source: "web", thread_key: "", actor: "", text: "" });

  const { data: detail } = useFetch<V2Thread>(selected ? `/api/v2/threads/${selected}` : null);

  const submitInbound = async () => {
    try {
      await v2Post("/threads/inbound", form);
      message.success("入站事件已记录");
      setForm({ ...form, text: "" });
      refetch();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  return (
    <div>
      <PageHeader index="03 · V2 THREADS" title="线程" subtitle="Inbound Threads" />
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1.4fr", gap: 14, alignItems: "start" }}>
        <div className="at-panel" style={{ padding: 14 }}>
          <div className="at-stat-label">Threads · {threads?.length ?? 0}</div>
          {(threads || []).map((t) => (
            <div key={t.id} onClick={() => setSelected(t.id)}
              style={{ padding: "8px 10px", margin: "0 -10px", cursor: "pointer", borderRadius: 2,
                background: selected === t.id ? "var(--at-bg-deep)" : "transparent" }}
              onMouseEnter={(e) => { e.currentTarget.style.background = "var(--at-bg-deep)"; }}
              onMouseLeave={(e) => { if (selected !== t.id) e.currentTarget.style.background = "transparent"; }}>
              <div style={{ fontFamily: "var(--at-font-mono)", fontSize: 13 }}>
                <span style={{ color: "var(--at-blue)" }}>{t.source}</span>
                <span style={{ color: "var(--at-text)" }}> / {t.thread_key}</span>
              </div>
              <div style={{ fontSize: 12, color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)" }}>
                {t.created_at}
              </div>
            </div>
          ))}

          {/* 手工入站(调试/演示;生产走 webhook) */}
          <div style={{ borderTop: "1px solid var(--at-border-soft)", marginTop: 12, paddingTop: 12 }}>
            <div className="at-stat-label" style={{ marginBottom: 8 }}>模拟入站</div>
            <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
              <Input size="small" value={form.thread_key} onChange={(e) => setForm({ ...form, thread_key: e.target.value })}
                placeholder="thread_key" />
              <Input size="small" value={form.actor} onChange={(e) => setForm({ ...form, actor: e.target.value })}
                placeholder="actor" />
            </div>
            <Input size="small" value={form.text} onChange={(e) => setForm({ ...form, text: e.target.value })}
              placeholder="入站文本" onPressEnter={submitInbound} />
            <button className="at-btn-amber" style={{ marginTop: 8, width: "100%" }} onClick={submitInbound}>
              + 记录入站事件
            </button>
          </div>
        </div>

        <div className="at-panel" style={{ padding: 14 }}>
          {!detail && <div style={{ color: "var(--at-text-faint)", fontSize: 13, textAlign: "center", padding: 30 }}>
            ← 选择一个线程
          </div>}
          {detail && (
            <>
              <div className="at-stat-label">Triggers · {detail.triggers?.length ?? 0}</div>
              {(detail.triggers || []).map((t) => (
                <div key={t.id} style={{ fontSize: 13, padding: "6px 0", borderBottom: "1px solid var(--at-border-soft)" }}>
                  <span style={{ fontFamily: "var(--at-font-mono)", color: "var(--at-amber)" }}>{t.actor}</span>
                  <span style={{ color: "var(--at-text-dim)", fontFamily: "var(--at-font-mono)", fontSize: 12, marginLeft: 8 }}>
                    {t.at}
                  </span>
                  <div style={{ color: "var(--at-text)" }}>{t.text}</div>
                </div>
              ))}
              <div className="at-stat-label" style={{ marginTop: 14 }}>工单 · {detail.tickets?.length ?? 0}</div>
              {(detail.tickets || []).map((t) => (
                <div key={t.id} style={{ fontSize: 13, padding: "6px 0", fontFamily: "var(--at-font-mono)" }}>
                  <a href={`/v2/tickets/${t.id}`} style={{ color: "var(--at-amber)" }}>
                    {t.title || t.id.slice(0, 8)} ↗
                  </a>
                  <span style={{ color: "var(--at-text-faint)", marginLeft: 8 }}>{t.status}</span>
                </div>
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
