import { useState } from "react";
import { Card, Table, Tag } from "antd";
import { useFetch } from "../hooks/useFetch";
import { api, type SkillItem, type SkillDetail } from "../api/client";

export default function Skills() {
  const { data, loading, error, refetch } = useFetch<{ skills: SkillItem[] }>(
    "/api/skills"
  );
  const [expandedName, setExpandedName] = useState<string | null>(null);
  const [skillContent, setSkillContent] = useState<SkillDetail | null>(null);
  const [contentLoading, setContentLoading] = useState(false);

  const handleExpand = async (name: string) => {
    if (expandedName === name) {
      setExpandedName(null);
      setSkillContent(null);
      return;
    }
    setExpandedName(name);
    setContentLoading(true);
    try {
      const detail = await api<SkillDetail>(`/api/skills/${encodeURIComponent(name)}`);
      setSkillContent(detail);
    } catch {
      setSkillContent(null);
    } finally {
      setContentLoading(false);
    }
  };

  if (error && !data) return <div>错误: {error}</div>;

  const skills = data?.skills || [];

  return (
    <div>
      <Card title="Skills" extra={<a onClick={() => refetch()}>刷新</a>}>
        <Table
          dataSource={skills}
          columns={[
            {
              title: "名称",
              dataIndex: "name",
              render: (name: string) => (
                <a onClick={() => handleExpand(name)}>{name}</a>
              ),
            },
            {
              title: "类型",
              key: "type",
              render: (_: unknown, r: SkillItem) =>
                r.name.startsWith("auto_") ? (
                  <Tag color="orange">自动生成</Tag>
                ) : (
                  <Tag color="blue">预置</Tag>
                ),
            },
          ]}
          rowKey="name"
          loading={loading}
          pagination={false}
          expandable={{
            expandedRowKeys: expandedName ? [expandedName] : [],
            onExpandedRowsChange: (keys) => {
              if (keys.length === 0) {
                setExpandedName(null);
                setSkillContent(null);
              }
            },
            expandedRowRender: () =>
              contentLoading ? (
                <div>加载中...</div>
              ) : skillContent ? (
                <pre
                  style={{
                    background: "#f5f5f5",
                    padding: 16,
                    borderRadius: 8,
                    fontSize: 13,
                    maxHeight: 480,
                    overflow: "auto",
                    whiteSpace: "pre-wrap",
                  }}
                >
                  {skillContent.content}
                </pre>
              ) : (
                <div>无法加载内容</div>
              ),
          }}
        />
      </Card>
    </div>
  );
}
