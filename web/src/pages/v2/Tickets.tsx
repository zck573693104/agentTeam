/**
 * V2 工单看板:按状态分列 + 新建请求 + 组织过滤(领导看下属:选节点即看其子树全部工单)。
 * dsh: Ticket = 已授权工作单元,状态机独立于执行器。
 */
import { useState } from "react";
import { Input, Modal, Select, message } from "antd";
import { Link } from "react-router-dom";
import { useFetch } from "../../hooks/useFetch";
import { v2Post, type V2OrgNode, type V2Ticket } from "../../api/v2";
import { PageHeader } from "../../components/PageHeader";

const COLUMNS: { status: string; label: string; color: string }[] = [
  { status: "pending", label: "待执行", color: "var(--at-gray)" },
  { status: "running", label: "执行中", color: "var(--at-green)" },
  { status: "waiting_human", label: "等待人工", color: "var(--at-amber)" },
  { status: "done", label: "已完成", color: "var(--at-gold)" },
  { status: "failed", label: "失败", color: "var(--at-red)" },
  { status: "cancelled", label: "已撤权", color: "var(--at-text-faint)" },
];

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("zh-CN", { hour12: false });
}

function NewRequestModal({ open, onClose, onDone }: {
  open: boolean; onClose: () => void; onDone: () => void;
}) {
  const [text, setText] = useState("");
  const [worker, setWorker] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    try {
      const resp = await v2Post<{ action: string; reply?: string; ticket_id?: string }>(
        "/requests", { text, worker_name: worker.trim() || undefined, submitted_by: "web" },
      );
      if (resp.action === "reply") {
        Modal.info({ title: "Router 直接答复", content: resp.reply });
      } else {
        message.success(`工单已创建 ${resp.ticket_id?.slice(0, 8)}`);
      }
      setText("");
      setWorker("");
      onDone();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title="新请求(Router 接单)"
      open={open}
      onCancel={onClose}
      onOk={submit}
      confirmLoading={busy}
      okText="提交"
      cancelText="取消"
    >
      <Input.TextArea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={5}
        placeholder="描述任务…Router 会决定直接答复或派工(不指定 worker 时由 Router 路由)"
      />
      <div style={{ marginTop: 10 }}>
        <Input
          value={worker}
          onChange={(e) => setWorker(e.target.value)}
          placeholder="worker 名(可选,留空由 Router 路由)"
        />
      </div>
    </Modal>
  );

}

function TicketCard({ t }: { t: V2Ticket }) {
  return (
    <Link to={`/v2/tickets/${t.id}`} style={{ textDecoration: "none" }}>
      <div className="at-panel" style={{ padding: "10px 12px", marginBottom: 8, cursor: "pointer" }}
        onMouseEnter={(e) => { e.currentTarget.style.borderColor = "var(--at-amber)"; }}
        onMouseLeave={(e) => { e.currentTarget.style.borderColor = ""; }}>
        <div style={{ fontFamily: "var(--at-font-mono)", fontSize: 13, color: "var(--at-text)",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {t.title || t.task.slice(0, 40)}
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 4, fontSize: 12, color: "var(--at-text-faint)",
          fontFamily: "var(--at-font-mono)" }}>
          <span>{t.worker_name || "—"}</span>
          <span>·</span>
          <span>{fmtTime(t.updated_at)}</span>
          {t.mandate && (
            <span style={{ color: t.mandate.basis === "inferred" ? "var(--at-blue)" : "var(--at-text-faint)" }}>
              · {t.mandate.basis}
            </span>
          )}
        </div>
      </div>
    </Link>
  );
}

export default function V2Tickets() {
  const [modalOpen, setModalOpen] = useState(false);
  const [orgFilter, setOrgFilter] = useState<string | undefined>(undefined);
  const { data, loading, refetch } = useFetch<V2Ticket[]>("/api/tickets");
  const { data: orgData } = useFetch<{ tree: V2OrgNode[]; workers_by_node: Record<string, { name: string }[]> }>("/api/org");

  // 组织树扁平化(过滤选项) + 节点子树工人集合(按组看下属工单)
  const nodeOptions: { id: string; label: string }[] = [];
  const flatten = (nodes: V2OrgNode[], depth: number) => {
    for (const n of nodes) {
      nodeOptions.push({ id: n.id, label: `${"　".repeat(depth)}${n.name}` });
      flatten(n.children || [], depth + 1);
    }
  };
  flatten(orgData?.tree || [], 0);
  const subtreeWorkers = (nodeId: string | undefined): Set<string> | null => {
    if (!nodeId || !orgData) return null;
    const ids = new Set<string>([nodeId]);
    const addSub = (nodes: V2OrgNode[]) => {
      for (const n of nodes) {
        if (n.id === nodeId || ids.has(n.parent_id || "")) {
          ids.add(n.id);
          addSub(n.children || []);
        }
      }
    };
    addSub(orgData.tree || []);
    const names = new Set<string>();
    for (const [nid, ws] of Object.entries(orgData.workers_by_node || {})) {
      if (ids.has(nid)) for (const w of ws) names.add(w.name);
    }
    return names;
  };
  const activeWorkers = subtreeWorkers(orgFilter);

  const filtered = activeWorkers
    ? (data || []).filter((t) => t.worker_name && activeWorkers.has(t.worker_name))
    : (data || []);

  return (
    <div>
      <PageHeader
        index="03 · V2 TICKETS"
        title="工单看板"
        subtitle="Ticket Board"
        action={
          <span style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <Select size="small" style={{ minWidth: 170 }} allowClear
              placeholder="组织过滤(看下属工单)"
              value={orgFilter}
              onChange={(v) => setOrgFilter(v || undefined)}
              options={nodeOptions.map((n) => ({ value: n.id, label: n.label }))} />
            <button className="at-btn-amber" onClick={() => setModalOpen(true)}>+ 新请求</button>
          </span>
        }
      />
      {loading && !data && (
        <div style={{ padding: 40, textAlign: "center", color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 13 }}>
          <span style={{ color: "var(--at-amber)" }}>●</span> LOADING...
        </div>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 12 }}>
        {COLUMNS.map((col) => {
          const list = filtered.filter((t) => t.status === col.status);
          return (
            <div key={col.status} className="at-panel at-fade-in" style={{ padding: 12, minHeight: 120 }}>
              <div style={{
                fontFamily: "var(--at-font-mono)", fontSize: 12, letterSpacing: "0.12em",
                color: col.color, marginBottom: 10, textTransform: "uppercase",
              }}>
                {col.label} · {list.length}
              </div>
              {list.map((t) => <TicketCard key={t.id} t={t} />)}
              {list.length === 0 && (
                <div style={{ color: "var(--at-text-faint)", fontSize: 12, textAlign: "center", padding: 12 }}>
                  —
                </div>
              )}
            </div>
          );
        })}
      </div>
      <NewRequestModal open={modalOpen} onClose={() => setModalOpen(false)} onDone={refetch} />
    </div>
  );
}