/** V2 入站源:webhook/github/feishu 配置 + 秘钥(dsh Source 协议)。 */
import { useState } from "react";
import { Form, Input, Modal, Select, message, Popconfirm } from "antd";
import { useFetch } from "../../hooks/useFetch";
import { v2Delete, v2Post, type V2Source } from "../../api/v2";
import { PageHeader } from "../../components/PageHeader";

function SourceModal({ open, onClose, onDone }: {
  open: boolean; onClose: () => void; onDone: () => void;
}) {
  const [form] = Form.useForm();
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    const v = await form.validateFields();
    setBusy(true);
    try {
      await v2Post("/sources", {
        name: v.name.trim(),
        kind: v.kind,
        secret: v.secret || undefined,
        config: {
          mode: v.mode || "observe",
          allowed_actors: v.allowed_actors || ["*"],
          keywords: v.keywords || ["*"],
        },
      });
      message.success("Source 已创建");
      form.resetFields();
      onDone();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="添加入站源" open={open} onCancel={onClose} onOk={submit}
      confirmLoading={busy} okText="创建" cancelText="取消" width={560} destroyOnHidden>
      <Form form={form} layout="vertical" requiredMark={false}>
        <div style={{ display: "flex", gap: 12 }}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "必填" }]}
            style={{ flex: 1 }}>
            <Input placeholder="如 ops_webhook" />
          </Form.Item>
          <Form.Item name="kind" label="类型" initialValue="webhook" style={{ flex: "0 0 160px" }}>
            <Select options={[
              { value: "webhook", label: "Webhook(push)" },
              { value: "github", label: "GitHub(poll 骨架)" },
              { value: "feishu", label: "飞书(poll 骨架)" },
            ]} />
          </Form.Item>
        </div>
        <div style={{ display: "flex", gap: 12 }}>
          <Form.Item name="mode" label="立项模式" initialValue="observe" style={{ flex: "0 0 200px" }}>
            <Select options={[
              { value: "observe", label: "观察模式(默认,只记录)" },
              { value: "auto", label: "auto(直接立项)" },
            ]} />
          </Form.Item>
          <Form.Item name="secret" label="Webhook 秘钥(可选)" style={{ flex: 1 }}>
            <Input.Password placeholder="留空 = 开放(仅开发态)" />
          </Form.Item>
        </div>
        <Form.Item name="allowed_actors" label="Actor 白名单(逗号分隔,* = 全部)">
          <Select mode="tags" tokenSeparators={[","]} placeholder="留空 = 全部放行" />
        </Form.Item>
        <Form.Item name="keywords" label="关键词快筛(逗号分隔,* = 全部)">
          <Select mode="tags" tokenSeparators={[","]} placeholder="留空 = 全部放行" />
        </Form.Item>
      </Form>
    </Modal>
  );
}

export default function V2Sources() {
  const { data, loading, refetch } = useFetch<V2Source[]>("/api/v2/sources");
  const [modalOpen, setModalOpen] = useState(false);

  const remove = async (name: string) => {
    try {
      await v2Delete(`/sources/${name}`);
      message.success("已删除");
      refetch();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  return (
    <div>
      <PageHeader
        index="06 · V2 SOURCES"
        title="入站源"
        subtitle="Inbound Sources"
        action={<button className="at-btn-amber" onClick={() => setModalOpen(true)}>+ 添加 Source</button>}
      />
      <div style={{ fontSize: 13, color: "var(--at-text-dim)", marginBottom: 14, fontFamily: "var(--at-font-mono)" }}>
        webhook 入口: POST <span style={{ color: "var(--at-amber)" }}>/api/v2/sources/&lt;name&gt;/webhook</span>
        (header X-Source-Secret);观察模式先记录 would-accept。
      </div>
      {loading && !data && (
        <div style={{ padding: 40, textAlign: "center", color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 13 }}>
          <span style={{ color: "var(--at-amber)" }}>●</span> LOADING...
        </div>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(360px, 1fr))", gap: 14 }}>
        {(data || []).map((s, i) => (
          <div key={s.name} className={`at-panel at-panel-corners at-fade-in at-fade-in-${Math.min(i + 1, 4)}`}
            style={{ padding: 16 }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
              <span style={{ fontFamily: "var(--at-font-mono)", fontSize: 15, color: "var(--at-amber)" }}>
                {s.name}
              </span>
              <Popconfirm title="删除该 Source?" onConfirm={() => remove(s.name)}>
                <button className="at-link" style={{ color: "var(--at-red)" }}>✕ DEL</button>
              </Popconfirm>
            </div>
            <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
              <span className="at-tag" style={{ color: "var(--at-blue)" }}>{s.kind}</span>
              <span className="at-tag" style={{
                color: (s.config || {}).mode === "auto" ? "var(--at-green)" : "var(--at-amber)",
              }}>
                {(s.config || {}).mode || "observe"}
              </span>
              <span className="at-tag" style={{ color: s.enabled ? "var(--at-green)" : "var(--at-text-faint)" }}>
                {s.enabled ? "enabled" : "disabled"}
              </span>
            </div>
            <div style={{ fontSize: 12, fontFamily: "var(--at-font-mono)", color: "var(--at-text-faint)" }}>
              actors: {JSON.stringify((s.config || {}).allowed_actors || ["*"])}
              <br />
              keywords: {JSON.stringify((s.config || {}).keywords || ["*"])}
            </div>
          </div>
        ))}
      </div>
      <SourceModal open={modalOpen} onClose={() => setModalOpen(false)}
        onDone={() => { setModalOpen(false); refetch(); }} />
    </div>
  );
}
