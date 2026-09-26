import { useState } from "react";
import { Form, Input, Modal, Select, message } from "antd";
import { useNavigate } from "react-router-dom";
import { useFetch } from "../hooks/useFetch";
import { api, type Run, type Team } from "../api/client";
import StatusBadge from "../components/StatusBadge";

/** 页头组件:统一所有内页的顶部样式 */
export function PageHeader({
  index,
  title,
  subtitle,
  action,
}: {
  index: string;
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
}) {
  return (
    <header className="at-fade-in" style={{
      display: "flex",
      justifyContent: "space-between",
      alignItems: "flex-end",
      marginBottom: 24,
      paddingBottom: 18,
      borderBottom: "1px solid var(--at-border)",
    }}>
      <div>
        <div style={{
          fontFamily: "var(--at-font-mono)",
          fontSize: 10,
          color: "var(--at-amber)",
          letterSpacing: "0.24em",
          textTransform: "uppercase",
          marginBottom: 6,
        }}>
          // {index}
        </div>
        <h1 style={{
          fontFamily: "var(--at-font-sans)",
          fontSize: 28,
          fontWeight: 300,
          color: "var(--at-text)",
          margin: 0,
          letterSpacing: "-0.01em",
        }}>
          {title}
          {subtitle && (
            <span style={{ color: "var(--at-text-faint)", fontWeight: 300 }}> · {subtitle}</span>
          )}
        </h1>
      </div>
      {action}
    </header>
  );
}

export default function Runs() {
  const { data, loading, error, refetch } = useFetch<Run[]>("/api/runs");
  const { data: teams } = useFetch<Team[]>("/api/teams");
  const [modalOpen, setModalOpen] = useState(false);
  const [form] = Form.useForm();
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();

  if (error && !data) {
    return (
      <div style={{ padding: 40, color: "var(--at-red)", fontFamily: "var(--at-font-mono)", fontSize: 12 }}>
        ✕ SIGNAL ERROR: {error}
      </div>
    );
  }

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      const resp = await api<{ run_id: string }>("/api/runs", {
        method: "POST",
        body: JSON.stringify(values),
      });
      message.success(`已提交: ${resp.run_id.slice(0, 8)}`);
      setModalOpen(false);
      form.resetFields();
      refetch();
      navigate(`/runs/${resp.run_id}`);
    } catch (e) {
      if (e instanceof Error && e.message) message.error(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  const runs = data || [];

  return (
    <div>
      <PageHeader
        index="04 · RUNS"
        title="执行记录"
        subtitle="Mission Log"
        action={<button className="at-btn-amber" onClick={() => setModalOpen(true)}>+ 新任务</button>}
      />

      <section className="at-panel at-fade-in" style={{ padding: 0 }}>
        {runs.length === 0 ? (
          <div style={{
            padding: 56,
            textAlign: "center",
            color: "var(--at-text-faint)",
            fontFamily: "var(--at-font-mono)",
            fontSize: 12,
          }}>
            ◌ NO RUNS · 暂无执行记录
          </div>
        ) : (
          <>
            <div style={{
              display: "grid",
              gridTemplateColumns: "120px 1fr 140px 120px 160px",
              gap: 16,
              padding: "10px 20px",
              background: "var(--at-bg-elev)",
              fontFamily: "var(--at-font-mono)",
              fontSize: 10,
              color: "var(--at-text-faint)",
              letterSpacing: "0.14em",
              textTransform: "uppercase",
              borderBottom: "1px solid var(--at-border)",
            }}>
              <span>Run ID</span>
              <span>Task</span>
              <span>Team</span>
              <span>Status</span>
              <span style={{ textAlign: "right" }}>Created</span>
            </div>
            {runs.map((r: Run, idx: number) => (
              <div
                key={r.run_id}
                onClick={() => navigate(`/runs/${r.run_id}`)}
                style={{
                  display: "grid",
                  gridTemplateColumns: "120px 1fr 140px 120px 160px",
                  gap: 16,
                  padding: "12px 20px",
                  borderBottom: idx === runs.length - 1 ? "none" : "1px solid var(--at-border-soft)",
                  cursor: "pointer",
                  transition: "background 0.12s ease, box-shadow 0.12s ease",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = "var(--at-bg-hover)";
                  e.currentTarget.style.boxShadow = "inset 2px 0 0 var(--at-amber)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = "transparent";
                  e.currentTarget.style.boxShadow = "none";
                }}
              >
                <span style={{ fontFamily: "var(--at-font-mono)", fontSize: 11, color: "var(--at-amber)" }}>
                  {r.run_id.slice(0, 8)}
                </span>
                <span style={{ fontSize: 13, color: "var(--at-text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {r.task}
                </span>
                <span style={{ fontFamily: "var(--at-font-mono)", fontSize: 11, color: "var(--at-text-dim)" }}>
                  {r.team_name}
                </span>
                <span><StatusBadge status={r.status} /></span>
                <span style={{ fontFamily: "var(--at-font-mono)", fontSize: 10, color: "var(--at-text-faint)", textAlign: "right" }}>
                  {new Date(r.created_at).toLocaleString("zh-CN", { hour12: false })}
                </span>
              </div>
            ))}
          </>
        )}
        {loading && (
          <div style={{ padding: 16, textAlign: "center", color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 11 }}>
            <span style={{ color: "var(--at-amber)" }}>●</span> LOADING...
          </div>
        )}
      </section>

      <Modal
        title="提交新任务"
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => setModalOpen(false)}
        confirmLoading={submitting}
        okText="提交"
        cancelText="取消"
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="team_name" label="团队" rules={[{ required: true }]}>
            <Select
              placeholder="选择团队"
              options={(teams || []).map((t) => ({ label: t.name, value: t.name }))}
            />
          </Form.Item>
          <Form.Item name="task" label="任务描述" rules={[{ required: true }]}>
            <Input.TextArea rows={4} placeholder="输入任务描述" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
