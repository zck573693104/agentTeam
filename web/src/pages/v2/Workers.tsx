/** V2 工人池 CRUD(Router + 扁平工人池,v2 的"团队配置")。 */
import { useEffect, useState } from "react";
import { Form, Input, InputNumber, Modal, Select, message, Popconfirm } from "antd";
import { useFetch } from "../../hooks/useFetch";
import { v2Delete, v2Post, v2Put, type V2Worker } from "../../api/v2";
import { PageHeader } from "../Runs";

function WorkerModal({ open, editing, onClose, onDone }: {
  open: boolean;
  editing: V2Worker | null;
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
  const { data, loading, refetch } = useFetch<V2Worker[]>("/api/v2/workers");
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<V2Worker | null>(null);

  const remove = async (name: string) => {
    try {
      await v2Delete(`/workers/${name}`);
      message.success("已删除");
      refetch();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  return (
    <div>
      <PageHeader
        index="09 · V2 WORKERS"
        title="工人池"
        subtitle="Worker Pool"
        action={
          <button className="at-btn-amber" onClick={() => { setEditing(null); setModalOpen(true); }}>
            + 添加 worker
          </button>
        }
      />
      {loading && !data && (
        <div style={{ padding: 40, textAlign: "center", color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 13 }}>
          <span style={{ color: "var(--at-amber)" }}>●</span> LOADING...
        </div>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(380px, 1fr))", gap: 14 }}>
        {(data || []).map((w, i) => (
          <div key={w.name} className={`at-panel at-panel-corners at-fade-in at-fade-in-${Math.min(i + 1, 4)}`}
            style={{ padding: 16 }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
              <span style={{ fontFamily: "var(--at-font-mono)", fontSize: 15, color: "var(--at-amber)" }}>
                {w.name}
              </span>
              <div style={{ display: "flex", gap: 10 }}>
                <button className="at-link" onClick={() => { setEditing(w); setModalOpen(true); }}>✎ EDIT</button>
                <Popconfirm title="删除该 worker?" onConfirm={() => remove(w.name)}>
                  <button className="at-link" style={{ color: "var(--at-red)" }}>✕ DEL</button>
                </Popconfirm>
              </div>
            </div>
            <div style={{ fontSize: 13, color: "var(--at-text-dim)", marginBottom: 8, minHeight: 36, lineHeight: 1.5 }}>
              {w.system_prompt || <i style={{ color: "var(--at-text-faint)" }}>无职责说明</i>}
            </div>
            <div style={{ display: "flex", gap: 14, fontSize: 12, fontFamily: "var(--at-font-mono)", color: "var(--at-text-faint)" }}>
              <span>模型: {w.model ? `${w.model.provider}/${w.model.name}` : "默认"}</span>
              <span>迭代: {w.max_iterations}</span>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 8 }}>
              {(w.tools || []).map((t) => (
                <span key={t} className="at-tag" style={{ color: "var(--at-cyan)", fontSize: 12 }}>{t}</span>
              ))}
              {(w.approval_targets || []).map((t) => (
                <span key={t} className="at-tag" style={{ color: "var(--at-red)", fontSize: 12 }}>
                  审批:{t}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
      <WorkerModal
        open={modalOpen}
        editing={editing}
        onClose={() => { setModalOpen(false); setEditing(null); }}
        onDone={() => { setModalOpen(false); setEditing(null); refetch(); }}
      />
    </div>
  );
}
