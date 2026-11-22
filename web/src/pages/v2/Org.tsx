/**
 * V2 组织架构树(P6): 任意深度层级,每个节点可动态加子级;
 * 工人挂载在节点上(部门/组/小组/任意自定义层级)。
 */
import { useState } from "react";
import { Input, Popconfirm, Select, message } from "antd";
import { useFetch } from "../../hooks/useFetch";
import { v2Post, v2Put, v2Delete, type V2Worker } from "../../api/v2";
import { PageHeader } from "../../components/PageHeader";

interface OrgNode {
  id: string;
  parent_id: string | null;
  name: string;
  kind: string;
  position: number;
  children: OrgNode[];
}

interface OrgBody {
  tree: OrgNode[];
  workers_by_node: Record<string, V2Worker[]>;
}

const KIND_TAG: Record<string, { label: string; color: string }> = {
  department: { label: "部门", color: "var(--at-blue)" },
  group: { label: "组", color: "var(--at-green)" },
  team: { label: "小组", color: "var(--at-amber)" },
};

function NodeRow({
  node,
  depth,
  workersByNode,
  allWorkers,
  onChanged,
}: {
  node: OrgNode;
  depth: number;
  workersByNode: Record<string, V2Worker[]>;
  allWorkers: V2Worker[];
  onChanged: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState("");
  const [newKind, setNewKind] = useState("group");
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState(node.name);
  const [attachName, setAttachName] = useState<string | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const members = workersByNode[node.id] || [];
  const tag = KIND_TAG[node.kind] || { label: node.kind, color: "var(--at-cyan)" };

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      onChanged();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div style={{
        display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap",
        padding: "7px 10px", marginLeft: depth === 0 ? 0 : depth * 22,
        borderLeft: depth === 0 ? "none" : "1px solid var(--at-border)",
      }}>
        {/* 层级连线符号 */}
        <span style={{ color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 11, width: 14 }}>
          {depth === 0 ? "▣" : "└"}
        </span>
        {editing ? (
          <>
            <Input size="small" style={{ width: 180 }} value={editName}
              onChange={(e) => setEditName(e.target.value)} />
            <button className="at-btn-amber" disabled={busy || !editName.trim()}
              onClick={() => run(async () => {
                await fetch(`/api/org/${node.id}`, {
                  method: "PATCH", headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ name: editName.trim() }),
                }).then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); });
                setEditing(false);
              })}>存</button>
            <button className="at-link" onClick={() => setEditing(false)}>取消</button>
          </>
        ) : (
          <>
            <b style={{ fontSize: 14 }}>{node.name}</b>
            <span className="at-tag" style={{ color: tag.color, fontSize: 11 }}>{tag.label}</span>
          </>
        )}
        {/* 挂载工人 chips */}
        {members.map((m) => (
          <span key={m.name} className="at-tag"
            style={{ color: "var(--at-text-dim)", fontFamily: "var(--at-font-mono)", fontSize: 11 }}>
            {m.name}
          </span>
        ))}
        <span style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center" }}>
          {/* 工人挂载/换挂 */}
          <Select size="small" placeholder="挂载工人…" style={{ minWidth: 130 }} allowClear
            value={attachName}
            onChange={(name) => {
              setAttachName(undefined);
              if (!name) return;
              const w = allWorkers.find((x) => x.name === name);
              if (!w) return;
              void run(async () => {
                await v2Put(`/workers/${name}`, { ...w, org_node_id: node.id });
                message.success(`已挂载 ${name} → ${node.name}`);
              });
            }}
            options={allWorkers
              .filter((w) => !members.some((m) => m.name === w.name))
              .map((w) => ({ value: w.name, label: w.name }))} />
          <button className="at-link" disabled={busy} onClick={() => setAdding(!adding)}>＋子级</button>
          <button className="at-link" disabled={busy} onClick={() => { setEditing(true); setEditName(node.name); }}>改</button>
          <Popconfirm title="删除该节点?(需无子级且无挂载工人)"
            onConfirm={() => run(async () => {
              await v2Delete(`/org/${node.id}`);
              message.success("已删除");
            })}>
            <button className="at-link" style={{ color: "var(--at-red)" }}
              disabled={busy || node.children.length > 0 || members.length > 0}>删</button>
          </Popconfirm>
        </span>
      </div>
      {/* 动态加深: 任意节点(含新加的)都能继续加子级 → 层级深度无上限 */}
      {adding && (
        <div style={{
          marginLeft: (depth + 1) * 22 + 14, display: "flex", gap: 8,
          padding: "6px 10px", alignItems: "center",
        }}>
          <Input size="small" style={{ width: 200 }} placeholder="子级名称(组/小组/任意层级)"
            value={newName} onChange={(e) => setNewName(e.target.value)} />
          <Select size="small" style={{ width: 90 }} value={newKind}
            onChange={(k) => setNewKind(k)}
            options={[{ value: "group", label: "组" }, { value: "team", label: "小组" },
              { value: "department", label: "部门" }]} />
          <button className="at-btn-amber" disabled={busy || !newName.trim()}
            onClick={() => run(async () => {
              await v2Post("/org", { name: newName.trim(), kind: newKind, parent_id: node.id });
              setNewName("");
              setAdding(false);
            })}>加到「{node.name}」下</button>
          <button className="at-link" onClick={() => setAdding(false)}>取消</button>
        </div>
      )}
      {node.children.map((c) => (
        <NodeRow key={c.id} node={c} depth={depth + 1} workersByNode={workersByNode}
          allWorkers={allWorkers} onChanged={onChanged} />
      ))}
    </div>
  );
}

export default function V2Org() {
  const { data, loading, refetch } = useFetch<OrgBody>("/api/org");
  const { data: workers } = useFetch<V2Worker[]>("/api/workers");
  const [rootName, setRootName] = useState("");
  const [busy, setBusy] = useState(false);

  const addRoot = async () => {
    if (!rootName.trim()) return;
    setBusy(true);
    try {
      await v2Post("/org", { name: rootName.trim(), kind: "department" });
      setRootName("");
      message.success("已新增部门");
      refetch();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <PageHeader
        index="10 · V2 ORG"
        title="组织架构"
        subtitle="Org Tree · 任意深度"
        action={<button className="at-btn-amber" onClick={refetch}>⟳ 刷新</button>}
      />
      {loading && !data && (
        <div style={{ padding: 40, textAlign: "center", color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 13 }}>
          <span style={{ color: "var(--at-amber)" }}>●</span> LOADING...
        </div>
      )}
      {data && (
        <div className="at-panel at-panel-corners" style={{ padding: 16 }}>
          {/* 新根部门 */}
          <div style={{ display: "flex", gap: 8, marginBottom: 12, alignItems: "center" }}>
            <Input size="small" style={{ width: 220 }} placeholder="新部门名称"
              value={rootName} onChange={(e) => setRootName(e.target.value)} />
            <button className="at-btn-amber" disabled={busy || !rootName.trim()} onClick={addRoot}>
              ＋新增根部门
            </button>
            <span style={{ fontSize: 12, color: "var(--at-text-faint)" }}>
              每个节点都可「＋子级」继续加深 —— 层级深度无上限
            </span>
          </div>
          {(data.tree || []).map((n) => (
            <NodeRow key={n.id} node={n} depth={0} workersByNode={data.workers_by_node || {}}
              allWorkers={workers || []} onChanged={refetch} />
          ))}
          {data.tree.length === 0 && (
            <div style={{ padding: 30, textAlign: "center", color: "var(--at-text-faint)", fontSize: 13 }}>
              ◌ EMPTY · 尚无组织节点,先新增根部门
            </div>
          )}
        </div>
      )}
    </div>
  );
}
