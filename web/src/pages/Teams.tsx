import { useState } from "react";
import {
  Button,
  Card,
  Descriptions,
  Input,
  Modal,
  Popconfirm,
  Table,
  Tabs,
  Tag,
  message,
} from "antd";
import { useFetch } from "../hooks/useFetch";
import { api, type AgentNode, type Team, type TeamRefNode } from "../api/client";
import EvolutionHistory from "../components/EvolutionHistory";

/** 统计 Agent 树中 worker 节点数量。 */
function countWorkers(node: AgentNode | TeamRefNode): number {
  if ("_type" in node) return 0; // TeamRef 不计入 worker
  if (node.role === "worker") return 1;
  return (node.children || []).reduce((sum, c) => sum + countWorkers(c), 0);
}

/** 收集 Agent 树中所有 worker 节点（叶子）。 */
function collectWorkers(node: AgentNode | TeamRefNode): { name: string; role?: string }[] {
  if ("_type" in node) return [{ name: node.alias || node.name, role: "subteam" }];
  if (node.role === "worker") return [{ name: node.name, role: node.role }];
  return (node.children || []).flatMap((c) => collectWorkers(c));
}

/** 渲染 Agent 树为缩进字符串。 */
function renderAgentTree(node: AgentNode | TeamRefNode, depth = 0): string {
  const indent = "  ".repeat(depth);
  if ("_type" in node) {
    return `${indent}↳ [TeamRef] ${node.alias || node.name}`;
  }
  const head = `${indent}- ${node.name} (${node.role})`;
  const childLines = (node.children || []).map((c) => renderAgentTree(c, depth + 1));
  return [head, ...childLines].join("\n");
}

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
    {
      title: "Worker 数",
      render: (_: unknown, r: Team) => countWorkers(r.root),
    },
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
            expandedRowRender: (r: Team) => {
              /** 收集所有 worker 名称用于 evolution。 */
              const workers = collectWorkers(r.root).filter(
                (w) => w.role !== "subteam"
              );
              return (
                <Tabs
                  items={[
                    {
                      key: "info",
                      label: "团队信息",
                      children: (
                        <Descriptions column={1} bordered size="small">
                          <Descriptions.Item label="Root">
                            {r.root.name} ({r.root.role})
                          </Descriptions.Item>
                          <Descriptions.Item label="Workers">
                            {collectWorkers(r.root)
                              .map((w) => `${w.name}(${w.role})`)
                              .join(", ")}
                          </Descriptions.Item>
                          <Descriptions.Item label="Agent 树">
                            <pre style={{ margin: 0, fontSize: 12 }}>
                              {renderAgentTree(r.root)}
                            </pre>
                          </Descriptions.Item>
                          {r.skills?.length > 0 && (
                            <Descriptions.Item label="Skills">
                              {r.skills.map((s) => (
                                <Tag key={s} color="blue">
                                  {s}
                                </Tag>
                              ))}
                            </Descriptions.Item>
                          )}
                        </Descriptions>
                      ),
                    },
                    {
                      key: "evolution",
                      label: "进化历史",
                      children: (
                        <div>
                          {workers.length === 0 ? (
                            <div style={{ color: "#999" }}>
                              该团队无 worker agent
                            </div>
                          ) : (
                            <Tabs
                              items={workers.map((w) => ({
                                key: w.name,
                                label: w.name,
                                children: (
                                  <EvolutionHistory
                                    agentName={w.name}
                                    onRollback={refetch}
                                  />
                                ),
                              }))}
                            />
                          )}
                        </div>
                      ),
                    },
                  ]}
                />
              );
            },
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
