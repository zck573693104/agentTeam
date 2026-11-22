/**
 * V2 工人池(树状 UI):工人按组织架构树挂载展示(P6 org_nodes),层级深度无上限。
 * - 部门色带 + 层级导引线 + 节点折叠(深树导航)
 * - 卡片悬浮态、空组虚线占位;未挂载工人独立分区
 * 树本体的增删改在 /v2/org(组织架构页);本页专注「人挂在哪」。
 */
import { useEffect, useState } from "react";
import { Form, Input, InputNumber, Modal, Popconfirm, Select, message } from "antd";
import { useFetch } from "../../hooks/useFetch";
import { v2Delete, v2Post, v2Put, type V2OrgNode, type V2Worker } from "../../api/v2";
import { PageHeader } from "../../components/PageHeader";

interface OrgBody {
  tree: V2OrgNode[];
  workers_by_node: Record<string, V2Worker[]>;
}

const KIND_TAG: Record<string, { label: string; color: string }> = {
  department: { label: "部门", color: "var(--at-blue)" },
  group: { label: "组", color: "var(--at-green)" },
  team: { label: "小组", color: "var(--at-amber)" },
};

/* —— 树 UI 样式(伪元素/悬浮态无法用内联样式,集中在此) —— */
const TREE_CSS = `
.wk-children { border-left: 2px solid var(--at-border-soft); margin-left: 11px; }
.wk-node-row { position: relative; }
.wk-node-row::before { content: ""; position: absolute; left: -12px; top: 50%;
  width: 10px; height: 2px; background: var(--at-border-soft); }
.wk-card { transition: border-color .15s ease, transform .15s ease, box-shadow .15s ease;
  border: 1px solid transparent; }
.wk-card:hover { border-color: var(--at-amber-dim); transform: translateY(-1px);
  box-shadow: 0 2px 10px rgba(0,0,0,.35); }
.wk-empty { border: 1px dashed var(--at-border); color: var(--at-text-faint);
  font-size: 12px; padding: 10px 14px; font-family: var(--at-font-mono); }
.wk-toggle { width: 16px; height: 16px; line-height: 14px; text-align: center;
  color: var(--at-text-faint); cursor: pointer; user-select: none;
  border: 1px solid var(--at-border); font-size: 10px;
  font-family: var(--at-font-mono); background: var(--at-bg-deep); }
.wk-toggle:hover { color: var(--at-amber); border-color: var(--at-amber-dim); }
`;

function memberCount(node: V2OrgNode, workersByNode: Record<string, V2Worker[]>): number {
  return (workersByNode[node.id]?.length ?? 0)
    + node.children.reduce((acc, c) => acc + memberCount(c, workersByNode), 0);
}

function collectIds(nodes: V2OrgNode[], acc: Set<string> = new Set()): Set<string> {
  for (const n of nodes) { acc.add(n.id); collectIds(n.children || [], acc); }
  return acc;
}

function WorkerCard({ w, allNodes, onEdit, onChanged }: {
  w: V2Worker;
  allNodes: { id: string; label: string }[];
  onEdit: (w: V2Worker) => void;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const reassign = (nodeId: string | undefined) => {
    setBusy(true);
    v2Put(`/workers/${w.name}`, { ...w, org_node_id: nodeId ?? null })
      .then(() => { message.success(nodeId ? "已换挂节点" : "已从节点摘除"); onChanged(); })
      .catch((e) => message.error((e as Error).message))
      .finally(() => setBusy(false));
  };
  const remove = () => {
    v2Delete(`/workers/${w.name}`)
      .then(() => { message.success("已删除"); onChanged(); })
      .catch((e) => message.error((e as Error).message));
  };
  return (
    <div className="at-panel wk-card" style={{ padding: "10px 12px", background: "var(--at-bg-deep)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6, gap: 8 }}>
        <span style={{ fontFamily: "var(--at-font-mono)", fontSize: 14, color: "var(--at-amber)" }}>
          {w.name}
        </span>
        <span style={{ display: "flex", gap: 8, alignItems: "center", flexShrink: 0 }}>
          <button className="at-link" onClick={(e) => { e.stopPropagation(); onEdit(w); }}>✎ 编辑</button>
          <Popconfirm title={`删除 worker ${w.name}?`} onConfirm={(e) => { e?.stopPropagation(); remove(); }}
            onCancel={(e) => e?.stopPropagation()}>
            <button className="at-link" style={{ color: "var(--at-red)" }}
              onClick={(e) => e.stopPropagation()}>✕</button>
          </Popconfirm>
        </span>
      </div>
      <div style={{
        fontSize: 12.5, color: "var(--at-text-dim)", marginBottom: 8, lineHeight: 1.5,
        display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden",
        minHeight: 19,
      }}>
        {w.system_prompt ? w.system_prompt.split("\n")[0]
          : <i style={{ color: "var(--at-text-faint)" }}>无职责说明</i>}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, alignItems: "center" }}>
        {(w.tools || []).map((t) => (
          <span key={t} className="at-tag" style={{ color: "var(--at-cyan)", fontSize: 11 }}>{t}</span>
        ))}
        {(w.approval_targets || []).map((t) => (
          <span key={t} className="at-tag" style={{ color: "var(--at-red)", fontSize: 11 }}>审批:{t}</span>
        ))}
        <Select size="small" style={{ marginLeft: "auto", minWidth: 118 }} placeholder="换挂节点…"
          value={w.org_node_id || undefined} disabled={busy} allowClear
          onClick={(e) => e.stopPropagation()}
          onChange={(v) => reassign(v)}
          options={allNodes.map((n) => ({ value: n.id, label: n.label }))} />
      </div>
    </div>
  );
}

function TreeNode({
  node,
  depth,
  workersByNode,
  allNodes,
  collapsed,
  onToggle,
  onEdit,
  onChanged,
}: {
  node: V2OrgNode;
  depth: number;
  workersByNode: Record<string, V2Worker[]>;
  allNodes: { id: string; label: string }[];
  collapsed: Set<string>;
  onToggle: (id: string) => void;
  onEdit: (w: V2Worker) => void;
  onChanged: () => void;
}) {
  const members = workersByNode[node.id] || [];
  const children = node.children || [];
  const total = memberCount(node, workersByNode);
  const isOpen = !collapsed.has(node.id);
  const tag = KIND_TAG[node.kind] || { label: node.kind, color: "var(--at-cyan)" };
  const isRoot = depth === 0;

  const header = (
    <div className="wk-node-row" style={{
      display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap",
      padding: isRoot ? "10px 12px" : "8px 10px",
      marginTop: isRoot ? 0 : 2,
      background: isRoot ? "var(--at-bg-elev)" : "transparent",
      borderLeft: isRoot ? "3px solid var(--at-amber)" : undefined,
      borderRadius: isRoot ? 3 : 0,
    }}>
      {children.length > 0 ? (
        <span className="wk-toggle" onClick={() => onToggle(node.id)}>{isOpen ? "−" : "+"}</span>
      ) : (
        <span style={{ width: 16 }} />
      )}
      <b style={{ fontSize: isRoot ? 15 : 13.5, color: isRoot ? "var(--at-text)" : "var(--at-text-dim)" }}>
        {node.name}
      </b>
      <span className="at-tag" style={{ color: tag.color, fontSize: 11 }}>{tag.label}</span>
      <span style={{
        marginLeft: "auto", fontSize: 11, fontFamily: "var(--at-font-mono)",
        color: total > 0 ? "var(--at-text-faint)" : "var(--at-text-faint)",
      }}>
        {total > 0 ? `${total} 人` : "空"}
      </span>
    </div>
  );

  return (
    <div>
      {header}
      {isOpen && (
        <>
          <div style={{
            marginLeft: depth === 0 ? 0 : (depth + 1) * 18,
            display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(330px, 1fr))",
            gap: 10, padding: "4px 10px 8px 14px",
          }}>
            {members.map((w) => (
              <WorkerCard key={w.name} w={w} allNodes={allNodes} onEdit={onEdit} onChanged={onChanged} />
            ))}
            {members.length === 0 && children.length === 0 && (
              <div className="wk-empty">◌ 空组 · 卡片可从下方「未挂载」换挂进来</div>
            )}
          </div>
          <div className={children.length > 0 && depth >= 0 ? "wk-children" : ""}
            style={{ marginLeft: depth === 0 ? 10 : 10 }}>
            {children.map((c) => (
              <TreeNode key={c.id} node={c} depth={depth + 1} workersByNode={workersByNode}
                allNodes={allNodes} collapsed={collapsed} onToggle={onToggle}
                onEdit={onEdit} onChanged={onChanged} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function WorkerModal({ open, editing, nodeOptions, onClose, onDone }: {
  open: boolean;
  editing: V2Worker | null;
  nodeOptions: { id: string; label: string }[];
  onClose: () => void;
  onDone: () => void;
}) {
  const [form] = Form.useForm();
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    const v = await form.validateFields();
    const payload = {
      name: v.name.trim(),
      system_prompt: v.system_prompt || "",
      provider: v.provider || "qwen",
      model_name: v.model_name || undefined,
      tools: v.tools || [],
      skills: v.skills || [],
      approval_targets: v.approval_targets || [],
      max_iterations: v.max_iterations ?? 10,
      enabled: true,
      org_node_id: v.org_node_id || null,
    };
    setBusy(true);
    try {
      if (editing) {
        await v2Put(`/workers/${editing.name}`, {
          ...payload,
          model: v.model_name ? { provider: payload.provider, name: v.model_name } : null,
        });
        message.success("已保存(在途工单将记授权依据过期注记)");
      } else {
        await v2Post("/workers", {
          ...payload,
          model: v.model_name ? { provider: payload.provider, name: v.model_name } : null,
        });
        message.success("worker 已添加");
      }
      onDone();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  // 打开时回填(编辑模式;新建清空)
  useEffect(() => {
    if (!open) return;
    if (editing) {
      form.setFieldsValue({
        ...editing,
        provider: editing.model?.provider ?? "qwen",
        model_name: editing.model?.name ?? "",
        org_node_id: editing.org_node_id ?? undefined,
      });
    } else {
      form.resetFields();
    }
  }, [open, editing, form]);

  return (
    <Modal
      title={editing ? `编辑 worker · ${editing.name}` : "添加 worker"}
      open={open}
      onCancel={onClose}
      onOk={submit}
      confirmLoading={busy}
      okText={editing ? "保存" : "添加"}
      cancelText="取消"
      width={640}
      destroyOnHidden
    >
      <Form form={form} layout="vertical" requiredMark={false}>
        <div style={{ display: "flex", gap: 12 }}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "必填" }]}
            style={{ flex: "0 0 200px" }}>
            <Input placeholder="如 coder" disabled={!!editing} />
          </Form.Item>
          <Form.Item name="max_iterations" label="迭代上限" style={{ flex: "0 0 140px" }}>
            <InputNumber min={1} max={50} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="org_node_id" label="所属组织节点" style={{ flex: 1 }}>
            <Select allowClear placeholder="可选;树状展示挂载位置"
              options={nodeOptions.map((n) => ({ value: n.id, label: n.label }))} />
          </Form.Item>
        </div>
        <Form.Item name="system_prompt" label="职责说明(System Prompt)">
          <Input.TextArea autoSize={{ minRows: 3, maxRows: 6 }}
            placeholder="一句话职责 + 工具使用说明(dsh: worker 不与人对话,汇报进工单轨迹)" />
        </Form.Item>
        <div style={{ display: "flex", gap: 12 }}>
          <Form.Item name="provider" label="Provider" style={{ flex: "0 0 140px" }}>
            <Input placeholder="qwen" />
          </Form.Item>
          <Form.Item name="model_name" label="模型名(留空用默认)" style={{ flex: 1 }}>
            <Input placeholder="qwen-max" />
          </Form.Item>
        </div>
        <Form.Item name="tools" label="工具(文件工具每工单自动注入,此处声明其他)">
          <Select mode="tags" tokenSeparators={[","]} placeholder="如 search_web、mcp:git:git_status" />
        </Form.Item>
        <Form.Item name="approval_targets" label="需人工审批的工具(P1 审批门;留空免审批)">
          <Select mode="tags" tokenSeparators={[","]}
            placeholder="如 write_file;['*'] 表示全部工具" />
        </Form.Item>
        <Form.Item name="skills" label="Skills(skills/ 目录 .md 文件名)">
          <Select mode="tags" tokenSeparators={[","]} placeholder="可选" />
        </Form.Item>
      </Form>
    </Modal>
  );
}

export default function V2Workers() {
  const { data: orgData, loading: orgLoading, refetch: refetchOrg } = useFetch<OrgBody>("/api/org");
  const { data: workers, loading: wkLoading, refetch: refetchWorkers } = useFetch<V2Worker[]>("/api/workers");
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<V2Worker | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const refetch = () => { refetchOrg(); refetchWorkers(); };
  const toggle = (id: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const allIds = collectIds(orgData?.tree || []);

  // 树扁平化(编辑弹窗的所属节点选项,缩进表示层级)
  const nodeOptions: { id: string; label: string }[] = [];
  const flatten = (nodes: V2OrgNode[], depth: number) => {
    for (const n of nodes) {
      nodeOptions.push({ id: n.id, label: `${"　".repeat(depth)}${n.name}` });
      flatten(n.children || [], depth + 1);
    }
  };
  flatten(orgData?.tree || [], 0);
  const nodesById = new Set(nodeOptions.map((n) => n.id));

  const unattached = (workers || []).filter(
    (w) => !w.org_node_id || !nodesById.has(w.org_node_id),
  );
  const loading = orgLoading || wkLoading;

  return (
    <div>
      <style>{TREE_CSS}</style>
      <PageHeader
        index="06 · V2 WORKERS"
        title="工人池"
        subtitle="Worker Pool · 组织树"
        action={
          <span style={{ display: "flex", gap: 10 }}>
            <button className="at-link" style={{ fontSize: 12 }}
              onClick={() => setCollapsed(new Set())}>展开全部</button>
            <button className="at-link" style={{ fontSize: 12 }}
              onClick={() => setCollapsed(new Set(allIds))}>收起全部</button>
            <button className="at-btn-amber" onClick={() => { setEditing(null); setModalOpen(true); }}>
              + 添加 worker
            </button>
          </span>
        }
      />
      {loading && !orgData && (
        <div style={{ padding: 40, textAlign: "center", color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 13 }}>
          <span style={{ color: "var(--at-amber)" }}>●</span> LOADING...
        </div>
      )}
      {orgData && (
        <div className="at-panel at-panel-corners" style={{ padding: "8px 14px 12px" }}>
          {(orgData.tree || []).map((n, i) => (
            <div key={n.id} style={{ marginTop: i > 0 ? 10 : 0 }}>
              <TreeNode node={n} depth={0} workersByNode={orgData.workers_by_node || {}}
                allNodes={nodeOptions} collapsed={collapsed} onToggle={toggle}
                onEdit={(w) => { setEditing(w); setModalOpen(true); }}
                onChanged={refetch} />
            </div>
          ))}
          {(orgData.tree || []).length === 0 && (
            <div className="wk-empty" style={{ textAlign: "center", padding: 24 }}>
              ◌ 尚无组织节点 — 去「组织架构」页新增根部门,或直接添加工人(落未挂载区)
            </div>
          )}
        </div>
      )}
      {/* 未挂载工人 */}
      {orgData && unattached.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <div className="at-stat-label" style={{ marginBottom: 8 }}>
            未挂载工人 · {unattached.length} 人(卡片右下可挂到任意组织节点)
          </div>
          <div style={{
            border: "1px dashed var(--at-border)", borderRadius: 4, padding: 10,
            display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(330px, 1fr))", gap: 10,
          }}>
            {unattached.map((w) => (
              <WorkerCard key={w.name} w={w} allNodes={nodeOptions}
                onEdit={(x) => { setEditing(x); setModalOpen(true); }} onChanged={refetch} />
            ))}
          </div>
        </div>
      )}
      <WorkerModal
        open={modalOpen}
        editing={editing}
        nodeOptions={nodeOptions}
        onClose={() => { setModalOpen(false); setEditing(null); }}
        onDone={() => { setModalOpen(false); setEditing(null); refetch(); }}
      />
    </div>
  );
}
