import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Button,
  Card,
  Descriptions,
  Input,
  Modal,
  Table,
  Tabs,
  message,
} from "antd";
import { useFetch } from "../hooks/useFetch";
import { api, type Approval, type Run } from "../api/client";
import StatusBadge from "../components/StatusBadge";
import SSEViewer from "../components/SSEViewer";

export default function RunDetail() {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();
  const { data: run, loading, error, refetch } = useFetch<Run>(
    runId ? `/api/runs/${runId}` : null
  );
  const { data: approvals, refetch: refetchApprovals } = useFetch<Approval[]>(
    runId ? `/api/runs/${runId}/approvals` : null
  );
  const [sseKey, setSseKey] = useState(0);
  const [rejectModalOpen, setRejectModalOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // 仅在加载"不同 run"时显示加载屏;同 run 的 refetch(如审批后)保留现有数据,
  // 避免 SSEViewer 卸载导致实时轨迹丢失。
  if (loading && (!run || run.run_id !== runId)) return <div>加载中...</div>;
  if (error && (!run || run.run_id !== runId)) return <div>错误: {error}</div>;
  if (!run) return <div>未找到</div>;

  const handleApprove = async (approved: boolean, reason?: string): Promise<boolean> => {
    if (!runId) return false;
    try {
      setSubmitting(true);
      await api(`/api/runs/${runId}/approve`, {
        method: "POST",
        body: JSON.stringify({ approved, reason: reason || null }),
      });
      message.success(approved ? "已通过" : "已拒绝");
      refetch();
      refetchApprovals();
      setSseKey((k) => k + 1); // 重连 SSE
      return true;
    } catch (e) {
      message.error((e as Error).message);
      return false;
    } finally {
      setSubmitting(false);
    }
  };

  const approvalColumns = [
    { title: "审批 ID", dataIndex: "id", render: (id: string) => id.slice(0, 8) },
    { title: "状态", dataIndex: "status" },
    {
      title: "请求时间",
      dataIndex: "requested_at",
      render: (t: string) => new Date(t).toLocaleString(),
    },
    {
      title: "决策时间",
      dataIndex: "decided_at",
      render: (t: string | null) => (t ? new Date(t).toLocaleString() : "-"),
    },
    { title: "决策者", dataIndex: "decider", render: (d: string | null) => d || "-" },
    { title: "原因", dataIndex: "reason", render: (r: string | null) => r || "-" },
  ];

  return (
    <div>
      <Button onClick={() => navigate("/runs")} style={{ marginBottom: 16 }}>
        ← 返回列表
      </Button>

      <Card title="Run 详情" style={{ marginBottom: 16 }}>
        <Descriptions column={2} bordered>
          <Descriptions.Item label="Run ID">{run.run_id}</Descriptions.Item>
          <Descriptions.Item label="团队">{run.team_name}</Descriptions.Item>
          <Descriptions.Item label="任务">{run.task}</Descriptions.Item>
          <Descriptions.Item label="状态">
            <StatusBadge status={run.status} />
          </Descriptions.Item>
          <Descriptions.Item label="创建时间">
            {new Date(run.created_at).toLocaleString()}
          </Descriptions.Item>
          <Descriptions.Item label="更新时间">
            {new Date(run.updated_at).toLocaleString()}
          </Descriptions.Item>
          <Descriptions.Item label="结束时间">
            {run.ended_at ? new Date(run.ended_at).toLocaleString() : "-"}
          </Descriptions.Item>
          <Descriptions.Item label="Token">{run.total_tokens}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Tabs
        items={[
          {
            key: "trace",
            label: "实时轨迹",
            children: (
              <SSEViewer
                runId={run.run_id}
                refreshKey={sseKey}
                onReconnect={() => setSseKey((k) => k + 1)}
              />
            ),
          },
          {
            key: "approvals",
            label: "审批记录",
            children: (
              <div>
                {run.status === "interrupted" && (
                  <div style={{ marginBottom: 16 }}>
                    <Button
                      type="primary"
                      loading={submitting}
                      onClick={() => handleApprove(true)}
                      style={{ marginRight: 8 }}
                    >
                      通过
                    </Button>
                    <Button
                      danger
                      loading={submitting}
                      onClick={() => setRejectModalOpen(true)}
                    >
                      拒绝
                    </Button>
                  </div>
                )}
                <Table
                  dataSource={approvals || []}
                  columns={approvalColumns}
                  rowKey="id"
                  pagination={false}
                />
              </div>
            ),
          },
        ]}
      />

      <Modal
        title="拒绝原因"
        open={rejectModalOpen}
        confirmLoading={submitting}
        onOk={async () => {
          const ok = await handleApprove(false, rejectReason);
          if (ok) {
            setRejectModalOpen(false);
            setRejectReason("");
          }
        }}
        onCancel={() => setRejectModalOpen(false)}
        okText="确认拒绝"
        cancelText="取消"
      >
        <Input.TextArea
          value={rejectReason}
          onChange={(e) => setRejectReason(e.target.value)}
          rows={3}
          placeholder="输入拒绝原因（可选）"
        />
      </Modal>
    </div>
  );
}
