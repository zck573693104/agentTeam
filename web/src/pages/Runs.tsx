import { useState } from "react";
import { Button, Card, Form, Input, Modal, Select, Table, message } from "antd";
import { useNavigate } from "react-router-dom";
import { useFetch } from "../hooks/useFetch";
import { api, type Run, type Team } from "../api/client";
import StatusBadge from "../components/StatusBadge";

export default function Runs() {
  const { data, loading, error, refetch } = useFetch<Run[]>("/api/runs");
  const { data: teams } = useFetch<Team[]>("/api/teams");
  const [modalOpen, setModalOpen] = useState(false);
  const [form] = Form.useForm();
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();

  if (error && !data) return <div>错误: {error}</div>;

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

  const columns = [
    { title: "Run ID", dataIndex: "run_id", render: (id: string) => id.slice(0, 8) },
    { title: "团队", dataIndex: "team_name" },
    { title: "任务", dataIndex: "task", ellipsis: true },
    { title: "状态", dataIndex: "status", render: (s: string) => <StatusBadge status={s} /> },
    {
      title: "创建时间",
      dataIndex: "created_at",
      render: (t: string) => new Date(t).toLocaleString(),
    },
  ];

  return (
    <div>
      <Card
        title="Run 列表"
        extra={
          <Button type="primary" onClick={() => setModalOpen(true)}>
            提交任务
          </Button>
        }
      >
        <Table
          dataSource={data || []}
          columns={columns}
          rowKey="run_id"
          loading={loading}
          onRow={(r: Run) => ({
            onClick: () => navigate(`/runs/${r.run_id}`),
            style: { cursor: "pointer" },
          })}
        />
      </Card>

      <Modal
        title="提交任务"
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => setModalOpen(false)}
        confirmLoading={submitting}
        okText="提交"
        cancelText="取消"
      >
        <Form form={form} layout="vertical">
          <Form.Item name="team_name" label="团队" rules={[{ required: true }]}>
            <Select
              placeholder="选择团队"
              options={(teams || []).map((t) => ({ label: t.name, value: t.name }))}
            />
          </Form.Item>
          <Form.Item name="task" label="任务" rules={[{ required: true }]}>
            <Input.TextArea rows={4} placeholder="输入任务描述" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
