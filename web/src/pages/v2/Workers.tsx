/**
 * V2 工人池(树状 UI · 下钻式):不一次性渲染整棵树,逐层下钻。
 * - 根视图 = 部门列表;点部门 → 看直属组;点组 → 看小组/该层挂载工人
 * - 面包屑逐级返回;工人卡片挂在「当前所在层」
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

/* 下钻 UI 样式(悬浮态无法用内联样式,集中在此) */
const DRILL_CSS = `
.wk-card { transition: border-color .15s ease, transform .15s ease, box-shadow .15s ease;
  border: 1px solid transparent; }
.wk-card:hover { border-color: var(--at-amber-dim); transform: translateY(-1px);
  box-shadow: 0 2px 10px rgba(0,0,0,.35); }
.wk-drill-row { transition: background .12s ease; cursor: pointer; }
.wk-drill-row:hover { background: var(--at-bg-hover); }
.wk-crumb { cursor: pointer; font-family: var(--at-font-mono); font-size: 13px; }
.wk-crumb:hover { color: var(--at-amber); }
.wk-empty { border: 1px dashed var(--at-border); color: var(--at-text-faint);
  font-size: 12px; padding: 10px 14px; font-family: var(--at-font-mono); }
`;

function memberCount(node: V2OrgNode, workersByNode: Record<string, V2Worker[]>): number {
  return (workersByNode[node.id]?.length ?? 0)
    + node.children.reduce((acc, c) => acc + memberCount(c, workersByNode), 0);
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

/** 下钻视图:只渲染当前层的直属工人 + 下一层子节点行(点行继续下钻)。 */
function DrillView({
  tree,
  workersByNode,
  path,
  onNavigate,
  allNodes,
  onEdit,
  onChanged,
}: {
  tree: V2OrgNode[];
  workersByNode: Record<string, V2Worker[]>;
  path: string[];
  onNavigate: (path: string[]) => void;
  allNodes: { id: string; label: string }[];
  onEdit: (w: V2Worker) => void;
  onChanged: () => void;
}) {
  const nodesById = new Map<string, V2OrgNode>();
  const index = (nodes: V2OrgNode[]) => {
    for (const n of nodes) { nodesById.set(n.id, n); index(n.children || []); }
  };
  index(tree);

  // 容错:路径上有节点已被删 → 截断到最近有效层
  const effPath: string[] = [];
  for (const id of path) {
    const node = nodesById.get(id);
    if (!node) break;
    const parentOk = effPath.length === 0
      ? node.parent_id === null
      : node.parent_id === effPath[effPath.length - 1];
    if (!parentOk) break;
    effPath.push(id);
  }
  const current = effPath.length ? nodesById.get(effPath[effPath.length - 1]) : null;
  const children = current ? current.children || [] : tree;
  const members = current ? (workersByNode[current.id] || []) : [];
  const kindTag = current ? (KIND_TAG[current.kind] || { label: current.kind, color: "var(--at-cyan)" }) : null;

  return (
    <div className="at-panel at-panel-corners" style={{ padding: "12px 16px 16px" }}>
      {/* 面包屑 */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 12, flexWrap: "wrap" }}>
        <span className="wk-crumb" style={{ color: effPath.length === 0 ? "var(--at-amber)" : "var(--at-text-faint)" }}
          onClick={() => onNavigate([])}>▣ 全部部门</span>
        {effPath.map((id, i) => {
          const n = nodesById.get(id)!;
          const isLast = i === effPath.length - 1;
          return (
            <span key={id} style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span style={{ color: "var(--at-text-faint)" }}>›</span>
              <span className="wk-crumb"
                style={{ color: isLast ? "var(--at-amber)" : "var(--at-text-faint)",
                         fontWeight: isLast ? 600 : 400 }}
                onClick={() => onNavigate(effPath.slice(0, i + 1))}>{n.name}</span>
            </span>
          );
        })}
        {current && (
          <span style={{ marginLeft: 8, display: "flex", gap: 8, alignItems: "center" }}>
            <span className="at-tag" style={{ color: kindTag!.color, fontSize: 11 }}>{kindTag!.label}</span>
            <span style={{ fontSize: 11, color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)" }}>
              直挂 {members.length} · 下辖 {children.length} · 共 {memberCount(current, workersByNode)} 人
            </span>
          </span>
        )}
      </div>

      {/* 当前层的直属工人 */}
      <div style={{
        display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(330px, 1fr))",
        gap: 10, marginBottom: children.length > 0 ? 14 : 0,
      }}>
        {members.map((w) => (
          <WorkerCard key={w.name} w={w} allNodes={allNodes} onEdit={onEdit} onChanged={onChanged} />
        ))}
      </div>

      {/* 下一层子节点(点行下钻) */}
      {children.length > 0 && (
        <div style={{ borderTop: "1px solid var(--at-border-soft)", paddingTop: 8 }}>
          <div className="at-stat-label" style={{ marginBottom: 6 }}>
            {current ? `「${current.name}」下辖` : "选择部门进入"}
          </div>
          {children.map((c) => {
            const direct = (workersByNode[c.id]?.length ?? 0);
            const total = memberCount(c, workersByNode);
            const tag = KIND_TAG[c.kind] || { label: c.kind, color: "var(--at-cyan)" };
            return (
              <div key={c.id} className="wk-drill-row" style={{
                display: "flex", alignItems: "center", gap: 10,
                padding: "9px 12px", borderBottom: "1px solid var(--at-border-soft)",
              }} onClick={() => onNavigate([...effPath, c.id])}>
                <span style={{ color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 12 }}>▸</span>
                <b style={{ fontSize: 13.5 }}>{c.name}</b>
                <span className="at-tag" style={{ color: tag.color, fontSize: 11 }}>{tag.label}</span>
                <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)" }}>
                  {total > 0 ? `直挂 ${direct} · 共 ${total} 人` : "空"}
                </span>
                <span style={{ color: "var(--at-text-faint)", fontSize: 12 }}>›</span>
              </div>
            );
          })}
        </div>
      )}

      {/* 空态 */}
      {children.length === 0 && members.length === 0 && (
        <div className="wk-empty" style={{ textAlign: "center", padding: 24 }}>
          {current ? `「${current.name}」为叶子节点且无挂载工人 — 可在本页添加工人时选此节点,或在组织架构页加子级`
            : "◌ 尚无组织节点 — 去「组织架构」页新增根部门"}
        </div>
      )}
      {children.length === 0 && members.length > 0 && (
        <div style={{ fontSize: 11, color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", marginTop: 10 }}>
          ▤ 已是最深层 · 以上为该节点全部挂载工人
        </div>
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
            <Select allowClear placeholder="可选;挂载到组织树任意层级"
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
  const [path, setPath] = useState<string[]>([]);   // 下钻路径:根视图 = []

  const refetch = () => { refetchOrg(); refetchWorkers(); };

  // 树扁平化(编辑弹窗/换挂的所属节点选项,缩进表示层级)
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
      <style>{DRILL_CSS}</style>
      <PageHeader
        index="06 · V2 WORKERS"
        title="工人池"
        subtitle="Worker Pool · 逐层下钻"
        action={
          <button className="at-btn-amber" onClick={() => { setEditing(null); setModalOpen(true); }}>
            + 添加 worker
          </button>
        }
      />
      {loading && !orgData && (
        <div style={{ padding: 40, textAlign: "center", color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 13 }}>
          <span style={{ color: "var(--at-amber)" }}>●</span> LOADING...
        </div>
      )}
      {orgData && (
        <DrillView
          tree={orgData.tree || []}
          workersByNode={orgData.workers_by_node || {}}
          path={path}
          onNavigate={setPath}
          allNodes={nodeOptions}
          onEdit={(w) => { setEditing(w); setModalOpen(true); }}
          onChanged={refetch}
        />
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
