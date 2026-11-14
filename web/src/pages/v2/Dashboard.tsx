/** V2 总览(B8): 工单/预算/worker/注意件/outbox/来源 状态指挥台(dsh 注意力经济:一眼全局)。 */
import { useFetch } from "../../hooks/useFetch";
import { Link } from "react-router-dom";
import { type V2Dashboard } from "../../api/v2";
import { PageHeader } from "../../components/PageHeader";

function StatCard({ label, value, sub, to, color }: {
  label: string; value: string | number; sub?: string; to?: string; color?: string;
}) {
  const inner = (
    <div className="at-panel at-panel-corners at-fade-in" style={{ padding: 16, height: "100%" }}>
      <div className="at-stat-label">{label}</div>
      <div className="at-stat-value" style={{ color: color || "var(--at-text)", fontSize: 30 }}>{value}</div>
      {sub && <div style={{ fontSize: 12, color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", marginTop: 6 }}>{sub}</div>}
    </div>
  );
  return to ? <Link to={to} style={{ textDecoration: "none" }}>{inner}</Link> : inner;
}

export default function V2Dashboard() {
  const { data, loading, refetch } = useFetch<V2Dashboard>("/api/dashboard");
  const tickets = data?.tickets.by_status || {};
  const outbox = data?.outbox || {};

  return (
    <div>
      <PageHeader
        index="01 · V2 OVERVIEW"
        title="总览"
        subtitle="Mission Control"
        action={<button className="at-btn-amber" onClick={refetch}>⟳ 刷新</button>}
      />
      {loading && !data && (
        <div style={{ padding: 40, textAlign: "center", color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 13 }}>
          <span style={{ color: "var(--at-amber)" }}>●</span> LOADING...
        </div>
      )}
      {data && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 12 }}>
            <StatCard label="待人工处理" value={data.attentions_pending}
              sub="注意件收件箱" to="/v2/attention"
              color={data.attentions_pending > 0 ? "var(--at-amber)" : "var(--at-green)"} />
            <StatCard label="执行中工单" value={(tickets.running || 0) + (tickets.pending || 0)}
              sub={`done ${tickets.done || 0} · failed ${tickets.failed || 0}`}
              to="/v2/tickets" color="var(--at-green)" />
            <StatCard label="Token 总消耗" value={data.tokens_total.toLocaleString()}
              sub="A1 预算记账(全部会话)" />
            <StatCard label="工人池" value={`${data.workers.enabled}/${data.workers.total}`}
              sub="启用/总数" to="/v2/workers" />
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 12, marginTop: 12 }}>
            <StatCard label="Outbox 外发" value={`${outbox.pending || 0} 待发`}
              sub={`sent ${outbox.sent || 0} · dropped ${outbox.dropped || 0}`}
              color={(outbox.dropped || 0) > 0 ? "var(--at-red)" : undefined} />
            <StatCard label="入站源" value={`${data.sources.enabled}/${data.sources.total}`}
              sub="启用/总数" to="/v2/sources" />
            <StatCard label="线程" value={data.threads} sub="入站上下文" to="/v2/threads" />
            <StatCard label="事件速率" value={data.events_recent_1h}
              sub="近 1 小时事件数" color="var(--at-cyan)" />
          </div>
          <div className="at-panel" style={{ padding: 16, marginTop: 12 }}>
            <div className="at-stat-label">工单状态分布</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {Object.entries(tickets).map(([status, n]) => (
                <span key={status} className="at-tag" style={{ fontSize: 12, padding: "4px 10px" }}>
                  {status} · {n}
                </span>
              ))}
              {Object.keys(tickets).length === 0 && (
                <span style={{ color: "var(--at-text-faint)", fontSize: 13 }}>暂无工单 — 到「工单看板」发起新请求</span>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
