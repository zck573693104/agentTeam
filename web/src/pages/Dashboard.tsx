import { Card, Col, Row, Statistic, Table } from "antd";
import { Pie, Column } from "@ant-design/charts";
import { useNavigate } from "react-router-dom";
import { useFetch } from "../hooks/useFetch";
import type { Dashboard as DashboardData, Run } from "../api/client";
import StatusBadge from "../components/StatusBadge";

const STATUS_LABELS: Record<string, string> = {
  pending: "等待中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  interrupted: "待审批",
};

export default function Dashboard() {
  const { data, loading, error, refetch } = useFetch<DashboardData>("/api/dashboard");
  const navigate = useNavigate();

  if (loading && !data) return <div>加载中...</div>;
  if (error && !data) return <div>错误: {error}</div>;
  if (!data) return null;

  const statusData = Object.entries(data.by_status).map(([k, v]) => ({
    type: STATUS_LABELS[k] || k,
    value: v,
  }));

  const teamData = Object.entries(data.by_team).map(([k, v]) => ({
    team: k,
    count: v,
  }));

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
      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={6}>
          <Card>
            <Statistic title="总 Run 数" value={data.total_runs} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="总 Token" value={data.total_tokens} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="运行中" value={data.by_status.running || 0} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="已完成" value={data.by_status.completed || 0} />
          </Card>
        </Col>
      </Row>

      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={12}>
          <Card title="状态分布">
            <Pie data={statusData} angleField="value" colorField="type" innerRadius={0.6} />
          </Card>
        </Col>
        <Col span={12}>
          <Card title="团队分布">
            <Column data={teamData} xField="team" yField="count" />
          </Card>
        </Col>
      </Row>

      <Card title="最近 Run" extra={<a onClick={() => refetch()}>刷新</a>}>
        <Table
          dataSource={data.recent_runs}
          columns={columns}
          rowKey="run_id"
          pagination={false}
          onRow={(r: Run) => ({
            onClick: () => navigate(`/runs/${r.run_id}`),
            style: { cursor: "pointer" },
          })}
        />
      </Card>
    </div>
  );
}
