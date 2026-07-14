import { useState } from "react";
import {
  Button,
  Card,
  Descriptions,
  Input,
  Modal,
  Popconfirm,
  Table,
  message,
} from "antd";
import { useFetch } from "../hooks/useFetch";
import { api, type Team } from "../api/client";

export default function Teams() {
  const { data, loading, error, refetch } = useFetch<Team[]>("/api/teams");
  const [modalOpen, setModalOpen] = useState(false);
  const [jsonText, setJsonText] = useState("");
  const [submitting, setSubmitting] = useState(false);

  if (error && !data) return <div>错误: {error}</div>;

  const handleRegister = async () => {
    let team: Record<string, unknown>;
    try {
      team = JSON.parse(jsonText);
    } catch {
      message.error("JSON 格式错误");
      return;
    }
    try {
      setSubmitting(true);
      await api("/api/teams", { method: "POST", body: JSON.stringify(team) });
      message.success("注册成功");
      setModalOpen(false);
      setJsonText("");
      refetch();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (name: string) => {
    try {
      await api(`/api/teams/${name}`, { method: "DELETE" });
      message.success("删除成功");
      refetch();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const columns = [
    { title: "名称", dataIndex: "name" },
    { title: "描述", dataIndex: "description" },
    { title: "Worker 数", render: (_: unknown, r: Team) => r.workers.length },
    {
      title: "操作",
      render: (_: unknown, r: Team) => (
        <Popconfirm title="确定删除?" onConfirm={() => handleDelete(r.name)}>
          <Button danger size="small">
            删除
          </Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <div>
      <Card
        title="团队管理"
        extra={
          <Button type="primary" onClick={() => setModalOpen(true)}>
            注册团队
          </Button>
        }
      >
        <Table
          dataSource={data || []}
          columns={columns}
          rowKey="name"
          loading={loading}
          expandable={{
            expandedRowRender: (r: Team) => (
              <Descriptions column={1} bordered size="small">
                <Descriptions.Item label="Leader">
                  {r.leader?.name} ({r.leader?.role})
                </Descriptions.Item>
                <Descriptions.Item label="Workers">
                  {r.workers.map((w) => `${w.name}(${w.role})`).join(", ")}
                </Descriptions.Item>
              </Descriptions>
            ),
          }}
        />
      </Card>

      <Modal
        title="注册团队"
        open={modalOpen}
        onOk={handleRegister}
        onCancel={() => setModalOpen(false)}
        confirmLoading={submitting}
        okText="注册"
        cancelText="取消"
      >
        <Input.TextArea
          value={jsonText}
          onChange={(e) => setJsonText(e.target.value)}
          placeholder={'粘贴 Team JSON，如: {"name": "dev", "description": "...", ...}'}
          rows={12}
        />
      </Modal>
    </div>
  );
}
