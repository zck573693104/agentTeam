/**
 * 成员明细抽屉:配置明细 × 运行情况合并视图。
 *
 * - 配置明细来自 GET /api/teams/{name}(root 树,含 system_prompt/模型/工具等);
 * - 运行情况来自 GET /api/teams/{name}/members/status(worker_start/end 聚合)。
 * teamName 为 null 时不请求;关闭即卸载,每次打开都拉最新数据。
 */
import { useEffect, useMemo, useState } from "react";
import { Drawer, Table, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { Link } from "react-router-dom";
import { useFetch } from "../hooks/useFetch";
import {
  type AgentNode,
  type MemberStatus,
  type Team,
  type TeamMemberStatus,
  type TeamRefNode,
} from "../api/client";

interface MemberRow {
  key: string;
  name: string;
  kind: "supervisor" | "worker" | "subteam";
  node: AgentNode | null;
  status: MemberStatus | null;
}

/** 收集 root 树全部节点:supervisor 在前,worker 叶子,TeamRef 记为 subteam。 */
function collectMembers(root: AgentNode | null): MemberRow[] {
  if (!root) return [];
  const rows: MemberRow[] = [];
  const walk = (node: AgentNode | TeamRefNode) => {
    if ("_type" in node) {
      rows.push({ key: node.alias || node.name, name: node.alias || node.name, kind: "subteam", node: null, status: null });
      return;
    }
    rows.push({ key: node.name, name: node.name, kind: node.role, node, status: null });
    for (const c of node.children || []) walk(c);
  };
  walk(root);
  return rows;
}

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", { hour12: false });
}

function modelText(node: AgentNode | null, team: Team | null): string {
  const m = node?.model || team?.default_model;
  if (!m) return "—";
  return `${m.provider ?? "?"}/${m.name ?? "?"}`;
}

const KIND_LABEL: Record<MemberRow["kind"], { text: string; color: string }> = {
  supervisor: { text: "主管", color: "var(--at-amber)" },
  worker: { text: "成员", color: "var(--at-green)" },
  subteam: { text: "子团队", color: "var(--at-blue)" },
};

export default function MemberDetailDrawer({
  teamName,
  onClose,
}: {
  teamName: string | null;
  onClose: () => void;
}) {
  const { data: team, error: teamError } = useFetch<Team>(
    teamName ? `/api/teams/${teamName}` : null,
  );
  const { data: statusData, error: statusError } = useFetch<TeamMemberStatus>(
    teamName ? `/api/teams/${teamName}/members/status` : null,
  );

  const members = useMemo(() => {
    const rows = collectMembers(team?.root ?? null);
    const byName = new Map<string, MemberStatus>();
    for (const m of statusData?.members || []) byName.set(m.name, m);
    return rows.map((r) => ({ ...r, status: byName.get(r.name) ?? null }));
  }, [team, statusData]);

  // 数据到位后默认全部展开,明细直接可见
  const [expandedKeys, setExpandedKeys] = useState<string[]>([]);
  useEffect(() => {
    if (members.length > 0) setExpandedKeys(members.map((m) => m.key));
  }, [members.length, team?.name]); // eslint-disable-line react-hooks/exhaustive-deps

  const columns: ColumnsType<MemberRow> = [
    {
      title: "成员",
      dataIndex: "name",
      width: 160,
      render: (name: string, row) => (
        <span style={{ fontFamily: "var(--at-font-mono)", fontSize: 13, fontWeight: row.kind === "supervisor" ? 700 : 500 }}>
          {name}
        </span>
      ),
    },
    {
      title: "角色",
      dataIndex: "kind",
      width: 90,
      render: (kind: MemberRow["kind"]) => (
        <span style={{ color: KIND_LABEL[kind].color, fontFamily: "var(--at-font-mono)", fontSize: 12 }}>
          {KIND_LABEL[kind].text}
        </span>
      ),
    },
    {
      title: "模型",
      key: "model",
      width: 150,
      render: (_, row) => (
        <span style={{ fontFamily: "var(--at-font-mono)", fontSize: 12, color: row.node ? "var(--at-text)" : "var(--at-text-faint)" }}>
          {row.kind === "subteam" ? "—" : modelText(row.node, team)}
        </span>
      ),
    },
    {
      title: "工具",
      key: "tools",
      width: 80,
      render: (_, row) => {
        if (row.kind === "subteam") return "—";
        const n = row.node?.tools?.length ?? 0;
        return <span style={{ fontFamily: "var(--at-font-mono)", fontSize: 12 }}>{n > 0 ? `${n} 个` : "无"}</span>;
      },
    },
    {
      title: "迭代上限",
      key: "max_iter",
      width: 90,
      render: (_, row) => (
        <span style={{ fontFamily: "var(--at-font-mono)", fontSize: 12 }}>
          {row.node ? row.node.max_iterations ?? 10 : "—"}
        </span>
      ),
    },
    {
      title: "参与 Run",
      key: "runs",
      width: 90,
      render: (_, row) => (
        <span style={{ fontFamily: "var(--at-font-mono)", fontSize: 12, color: "var(--at-cyan)" }}>
          {row.status ? row.status.runs_participated : "—"}
        </span>
      ),
    },
    {
      title: "开工/失败",
      key: "starts",
      width: 100,
      render: (_, row) =>
        row.status ? (
          <span style={{ fontFamily: "var(--at-font-mono)", fontSize: 12 }}>
            {row.status.starts}
            {" / "}
            <span style={{ color: row.status.failures > 0 ? "var(--at-red)" : "var(--at-text-faint)" }}>
              {row.status.failures}
            </span>
          </span>
        ) : (
          "—"
        ),
    },
    {
      title: "最近活跃",
      key: "last_active",
      width: 150,
      render: (_, row) => {
        if (!row.status?.last_active_at) return <span style={{ color: "var(--at-text-faint)" }}>无记录</span>;
        return (
          <span style={{ fontFamily: "var(--at-font-mono)", fontSize: 12 }}>
            {fmtTime(row.status.last_active_at)}
            {row.status.last_run_id && (
              <Link to={`/runs/${row.status.last_run_id}`} style={{ marginLeft: 6, fontSize: 12 }}>
                run ↗
              </Link>
            )}
          </span>
        );
      },
    },
  ];

  return (
    <Drawer
      title={
        <span style={{ fontFamily: "var(--at-font-mono)" }}>
          成员明细 · <span style={{ color: "var(--at-amber)" }}>{teamName}</span>
        </span>
      }
      open={!!teamName}
      onClose={onClose}
      width={880}
      destroyOnHidden
    >
      {teamError && (
        <div style={{ color: "var(--at-red)", fontFamily: "var(--at-font-mono)", fontSize: 13, padding: "12px 0" }}>
          ✕ 团队加载失败: {teamError}
        </div>
      )}
      {statusError && (
        <div style={{ color: "var(--at-amber)", fontFamily: "var(--at-font-mono)", fontSize: 12, padding: "8px 0" }}>
          ⚠ 成员运行情况加载失败: {statusError}
        </div>
      )}

      <div style={{ color: "var(--at-text-dim)", fontSize: 13, marginBottom: 12, lineHeight: 1.6 }}>
        {team?.description || "无描述"}
      </div>

      <Table<MemberRow>
        size="small"
        rowKey="key"
        dataSource={members}
        columns={columns}
        loading={!team}
        pagination={false}
        expandable={{
          expandedRowKeys: expandedKeys,
          onExpandedRowsChange: (keys) => setExpandedKeys(keys as string[]),
          expandedRowRender: (row) => <MemberDetailPanel row={row} team={team} />,
          rowExpandable: (row) => row.node !== null,
        }}
      />
    </Drawer>
  );
}

/** 展开行:完整配置明细(system_prompt/工具/审批策略/技能/进化代数)。 */
function MemberDetailPanel({ row, team }: { row: MemberRow; team: Team | null }) {
  const node = row.node;
  if (!node) return null;
  const approval = node.approval_policy;

  const copyPrompt = async () => {
    try {
      await navigator.clipboard.writeText(node.system_prompt || "");
      message.success("System Prompt 已复制");
    } catch {
      message.error("复制失败");
    }
  };

  return (
    <div style={{ padding: "4px 8px 8px 8px", display: "flex", flexDirection: "column", gap: 10 }}>
      {/* System Prompt */}
      <div>
        <div className="at-stat-label" style={{ marginBottom: 4, display: "flex", justifyContent: "space-between" }}>
          <span>System Prompt</span>
          <button className="at-link" onClick={copyPrompt}>⧉ 复制</button>
        </div>
        <pre
          style={{
            fontSize: 13,
            margin: 0,
            padding: 10,
            background: "var(--at-bg-deep)",
            border: "1px solid var(--at-border)",
            color: "var(--at-text-mono)",
            whiteSpace: "pre-wrap",
            lineHeight: 1.6,
            maxHeight: 220,
            overflow: "auto",
          }}
        >
          {node.system_prompt || "(未设置)"}
        </pre>
      </div>

      {/* 工具清单 */}
      <div>
        <div className="at-stat-label" style={{ marginBottom: 4 }}>Tools · {node.tools?.length ?? 0}</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
          {(node.tools || []).length === 0 && (
            <span style={{ color: "var(--at-text-faint)", fontSize: 12 }}>无工具</span>
          )}
          {(node.tools || []).map((t) => (
            <span key={t} className="at-tag" style={{ fontSize: 12 }}>{t}</span>
          ))}
        </div>
      </div>

      {/* 其余字段 */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: "8px 24px" }}>
        <DetailField label="模型覆盖">
          {node.model
            ? `${node.model.provider}/${node.model.name}${node.model.temperature != null ? ` (T=${node.model.temperature})` : ""}`
            : "无(用团队默认)"}
        </DetailField>
        <DetailField label="Skills">
          {(node.skills || []).length > 0 ? (node.skills || []).join(", ") : "无"}
        </DetailField>
        <DetailField label="审批策略">
          {approval ? `${approval.level}${approval.targets?.length ? ` · 目标: ${approval.targets.join(", ")}` : ""}` : "无"}
        </DetailField>
        <DetailField label="Maker/Checker 审查模型">
          {node.review_model ? `${node.review_model.provider}/${node.review_model.name}` : "无"}
          {node.review_tools?.length ? ` · 审查工具: ${node.review_tools.join(", ")}` : ""}
        </DetailField>
        <DetailField label="专家库模板 (ref)">{node.ref || "无"}</DetailField>
        <DetailField label="进化代数 (version)">v{node.version ?? 1}</DetailField>
        <DetailField label="MCP 挂载">
          {(node.mcp_servers || []).length > 0
            ? (node.mcp_servers || []).map((s) => s.name).join(", ")
            : "无"}
        </DetailField>
        <DetailField label="团队默认模型">
          {team?.default_model ? `${team.default_model.provider}/${team.default_model.name}` : "—"}
        </DetailField>
      </div>
    </div>
  );
}

function DetailField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="at-stat-label" style={{ marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 13, color: "var(--at-text)", lineHeight: 1.5 }}>{children}</div>
    </div>
  );
}
