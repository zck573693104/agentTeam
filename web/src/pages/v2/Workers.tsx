/**
 * V2 工人池(树状):工人按组织架构树挂载展示(P6 org_nodes),层级深度无上限;
 * 未挂载节点的工人落在「未挂载」区。每个节点可 ＋子级 动态加深(完整树管理在 /v2/org)。
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
    <div className="at-panel" style={{ padding: 12, background: "var(--at-bg-deep)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
        <span style={{ fontFamily: "var(--at-font-mono)", fontSize: 14, color: "var(--at-amber)" }}>
          {w.name}
        </span>
        <span style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <span style={{ fontSize: 11, fontFamily: "var(--at-font-mono)", color: "var(--at-text-faint)" }}>
            {w.model ? `${w.model.provider}/${w.model.name}` : "默认模型"} · 迭代 {w.max_iterations}
          </span>
          <button className="at-link" onClick={(e) => { e.stopPropagation(); onEdit(w); }}>✎</button>
          <Popconfirm title={`删除 worker ${w.name}?`} onConfirm={(e) => { e?.stopPropagation(); remove(); }}
            onCancel={(e) => e?.stopPropagation()}>
            <button className="at-link" style={{ color: "var(--at-red)" }}
              onClick={(e) => e.stopPropagation()}>✕</button>
          </Popconfirm>
        </span>
      </div>
      <div style={{ fontSize: 12.5, color: "var(--at-text-dim)", marginBottom: 8, lineHeight: 1.5 }}>
        {w.system_prompt ? w.system_prompt.split("\n")[0] : <i style={{ color: "var(--at-text-faint)" }}>无职责说明</i>}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, alignItems: "center" }}>
        {(w.tools || []).map((t) => (
          <span key={t} className="at-tag" style={{ color: "var(--at-cyan)", fontSize: 11 }}>{t}</span>
        ))}
        {(w.approval_targets || []).map((t) => (
          <span key={t} className="at-tag" style={{ color: "var(--at-red)", fontSize: 11 }}>审批:{t}</span>
        ))}
        <Select size="small" style={{ marginLeft: "auto", minWidth: 120 }} placeholder="换挂节点…"
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
  onEdit,
  onChanged,
}: {
  node: V2OrgNode;
  depth: number;
  workersByNode: Record<string, V2Worker[]>;
  allNodes: { id: string; label: string }[];
  onEdit: (w: V2Worker) => void;
  onChanged: () => void;
}) {
  const members = workersByNode[node.id] || [];
  const tag = KIND_TAG[node.kind] || { label: node.kind, color: "var(--at-cyan)" };
  return (
    <div>
      <div style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "8px 10px 4px", marginLeft: depth === 0 ? 0 : depth * 20,
        borderLeft: depth === 0 ? "none" : "1px solid var(--at-border)",
      }}>
        <span style={{ color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 11, width: 14 }}>
          {depth === 0 ? "▣" : "└"}
        </span>
        <b style={{ fontSize: 14 }}>{node.name}</b>
        <span className="at-tag" style={{ color: tag.color, fontSize: 11 }}>{tag.label}</span>
        <span style={{ fontSize: 11, color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)" }}>
          {members.length} 人
        </span>
      </div>
      <div style={{ marginLeft: depth === 0 ? 0 : (depth + 1) * 20, display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))", gap: 10, paddingBottom: 4 }}>
        {members.map((w) => (
          <WorkerCard key={w.name} w={w} allNodes={allNodes} onEdit={onEdit} onChanged={onChanged} />
        ))}
      </div>
      {node.children.map((c) => (
        <TreeNode key={c.id} node={c} depth={depth + 1} workersByNode={workersByNode}
          allNodes={allNodes} onEdit={onEdit} onChanged={onChanged} />
      ))}
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

  const refetch = () => { refetchOrg(); refetchWorkers(); };

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
      <PageHeader
        index="06 · V2 WORKERS"
        title="工人池"
        subtitle="Worker Pool · 组织树"
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
        <div className="at-panel at-panel-corners" style={{ padding: 16 }}>
          {(orgData.tree || []).map((n) => (
            <TreeNode key={n.id} node={n} depth={0} workersByNode={orgData.workers_by_node || {}}
              allNodes={nodeOptions}
              onEdit={(w) => { setEditing(w); setModalOpen(true); }}
              onChanged={refetch} />
          ))}
          {(orgData.tree || []).length === 0 && (
            <div style={{ padding: 24, textAlign: "center", color: "var(--at-text-faint)", fontSize: 13 }}>
              ◌ 尚无组织节点 — 去「组织架构」页新增根部门,或直接添加工人(落未挂载区)
            </div>
          )}
        </div>
      )}
      {/* 未挂载工人 */}
      {orgData && (
        <div style={{ marginTop: 14 }}>
          <div className="at-stat-label" style={{ marginBottom: 8 }}>
            未挂载工人 · {unattached.length} 人(卡片上可挂到任意组织节点)
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))", gap: 10 }}>
            {unattached.map((w) => (
              <WorkerCard key={w.name} w={w} allNodes={nodeOptions}
                onEdit={(x) => { setEditing(x); setModalOpen(true); }} onChanged={refetch} />
            ))}
          </div>
          {orgData && unattached.length === 0 && (
            <div style={{ padding: 14, textAlign: "center", color: "var(--at-text-faint)", fontSize: 12 }}>
              ✓ 全部工人已挂载到组织节点
            </div>
          )}
        </div>
      )}
      {/* 删除按钮放编辑弹窗外不直观 — 卡片点击进编辑,弹窗底部留删除 */}
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
