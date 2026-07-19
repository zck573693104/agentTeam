import { useState } from "react";
import {
  Button,
  Descriptions,
  Popconfirm,
  Table,
  Tag,
  message,
} from "antd";
import { useFetch } from "../hooks/useFetch";
import {
  api,
  type EvolutionRecord,
  type RollbackResult,
} from "../api/client";

/** 维度标签颜色映射。 */
const DIMENSION_COLORS: Record<string, string> = {
  prompt: "blue",
  params: "green",
  skill_gen: "orange",
  skill_select: "purple",
  rollback: "red",
};

const DIMENSION_LABELS: Record<string, string> = {
  prompt: "Prompt 优化",
  params: "参数调优",
  skill_gen: "Skill 生成",
  skill_select: "Skill 推荐",
  rollback: "回滚",
};

interface EvolutionHistoryProps {
  agentName: string;
  onRollback?: () => void;
}

export default function EvolutionHistory({
  agentName,
  onRollback,
}: EvolutionHistoryProps) {
  const { data, loading, error, refetch } = useFetch<{
    history: EvolutionRecord[];
  }>(`/api/agents/${encodeURIComponent(agentName)}/history?limit=50`);

  const [rollingBack, setRollingBack] = useState<number | null>(null);

  if (loading && !data) return <div>加载中...</div>;
  if (error && !data) return <div>错误: {error}</div>;

  const records = data?.history || [];

  if (records.length === 0) {
    return <div style={{ color: "#999" }}>暂无进化记录</div>;
  }

  const handleRollback = async (version: number) => {
    setRollingBack(version);
    try {
      const result = await api<RollbackResult>(
        `/api/agents/${encodeURIComponent(agentName)}/rollback?version=${version}`,
        { method: "POST" }
      );
      if (result.ok) {
        message.success(`已回滚到 v${version},当前版本 v${result.new_version}`);
        refetch();
        onRollback?.();
      }
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setRollingBack(null);
    }
  };

  const columns = [
    {
      title: "版本",
      dataIndex: "version",
      width: 60,
      render: (v: number) => `v${v}`,
    },
    {
      title: "维度",
      dataIndex: "dimension",
      width: 120,
      render: (d: string) => (
        <Tag color={DIMENSION_COLORS[d] || "default"}>
          {DIMENSION_LABELS[d] || d}
        </Tag>
      ),
    },
    {
      title: "状态",
      dataIndex: "success",
      width: 60,
      render: (s: boolean) =>
        s ? (
          <Tag color="success">成功</Tag>
        ) : (
          <Tag color="error">失败</Tag>
        ),
    },
    {
      title: "原因",
      dataIndex: "reason",
      ellipsis: true,
    },
    {
      title: "时间",
      dataIndex: "timestamp",
      width: 160,
      render: (t: string) => new Date(t).toLocaleString(),
    },
    {
      title: "操作",
      key: "action",
      width: 80,
      render: (_: unknown, r: EvolutionRecord) =>
        r.success ? (
          <Popconfirm
            title={`确认回滚到 v${r.version}?`}
            description="回滚将创建新版本,恢复该版本的 prompt 和 params 配置"
            onConfirm={() => handleRollback(r.version)}
          >
            <Button
              size="small"
              loading={rollingBack === r.version}
              disabled={rollingBack !== null}
            >
              回滚
            </Button>
          </Popconfirm>
        ) : null,
    },
  ];

  return (
    <Table
      dataSource={records}
      columns={columns}
      rowKey="id"
      size="small"
      pagination={false}
      expandable={{
        expandedRowRender: (r: EvolutionRecord) => (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="Before">
              <pre style={{ margin: 0, fontSize: 12, whiteSpace: "pre-wrap" }}>
                {r.before_value || "-"}
              </pre>
            </Descriptions.Item>
            <Descriptions.Item label="After">
              <pre style={{ margin: 0, fontSize: 12, whiteSpace: "pre-wrap" }}>
                {r.after_value || "-"}
              </pre>
            </Descriptions.Item>
            {r.diff && (
              <Descriptions.Item label="Diff">
                <pre
                  style={{
                    margin: 0,
                    fontSize: 12,
                    whiteSpace: "pre-wrap",
                    color: "#666",
                  }}
                >
                  {r.diff}
                </pre>
              </Descriptions.Item>
            )}
            {r.error && (
              <Descriptions.Item label="错误">
                <span style={{ color: "#ff4d4f" }}>{r.error}</span>
              </Descriptions.Item>
            )}
          </Descriptions>
        ),
      }}
    />
  );
}
