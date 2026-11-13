import { useState } from "react";
import { Popconfirm, Tabs, message } from "antd";
import { useFetch } from "../hooks/useFetch";
import { api, type AgentNode, type Team, type TeamRefNode } from "../api/client";
import EvolutionHistory from "../components/EvolutionHistory";
import TeamMemory from "../components/TeamMemory";
import TeamFormModal from "../components/TeamFormModal";
import MemberDetailDrawer from "../components/MemberDetailDrawer";
import { PageHeader } from "./Runs";

/** 收集 Agent 树中所有 worker 节点(叶子)。 */
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
  const [formOpen, setFormOpen] = useState(false);
  const [editingTeam, setEditingTeam] = useState<Team | null>(null);
  const [drawerTeam, setDrawerTeam] = useState<string | null>(null);

  if (error && !data) return <div>错误: {error}</div>;

  const openCreate = () => {
    setEditingTeam(null);
    setFormOpen(true);
  };

  const openEdit = (team: Team) => {
    setEditingTeam(team);
    setFormOpen(true);
  };

  const handleFormSuccess = (name: string) => {
    const wasCreate = editingTeam === null;
    setFormOpen(false);
    setEditingTeam(null);
    refetch();
    // 新增团队后直接打开成员明细,让成员情况立刻可见
    if (wasCreate) setDrawerTeam(name);
  };

  const handleDelete = async (name: string) => {
    try {
      await api(`/api/teams/${name}`, { method: "DELETE" });
      message.success("删除成功");
      if (drawerTeam === name) setDrawerTeam(null);
      refetch();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  return (
    <div>
      <PageHeader
        index="02 · TEAMS"
        title="团队配置"
        subtitle="Agent Squads"
        action={<button className="at-btn-amber" onClick={openCreate}>+ 注册团队</button>}
      />

      {error && (
        <div style={{ padding: 24, color: "var(--at-red)", fontFamily: "var(--at-font-mono)", fontSize: 13 }}>
          ✕ SIGNAL ERROR: {error}
        </div>
      )}

      {loading && !data && (
        <div style={{ padding: 40, color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 13, textAlign: "center" }}>
          <span style={{ color: "var(--at-amber)" }}>●</span> LOADING TEAMS...
        </div>
      )}

      {data && data.length === 0 && (
        <div className="at-panel at-fade-in" style={{
          padding: 56,
          textAlign: "center",
          color: "var(--at-text-faint)",
          fontFamily: "var(--at-font-mono)",
          fontSize: 13,
        }}>
          ◌ NO TEAMS · 暂无团队配置
        </div>
      )}

      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(360px, 1fr))",
        gap: 14,
      }}>
        {(data || []).map((team, idx) => {
          const workers = collectWorkers(team.root);
          const workerCount = workers.filter((w) => w.role !== "subteam").length;
          const hasSubteam = workers.some((w) => w.role === "subteam");
          return (
            <div
              key={team.name}
              className={`at-panel at-panel-corners at-fade-in at-fade-in-${Math.min(idx + 1, 4)}`}
              style={{ padding: 18 }}
            >
              {/* 卡片头:名称 + 编辑/删除 */}
              <div style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "flex-start",
                marginBottom: 14,
              }}>
                <div>
                  <div style={{
                    fontFamily: "var(--at-font-mono)",
                    fontSize: 12,
                    color: "var(--at-text-faint)",
                    letterSpacing: "0.2em",
                    marginBottom: 4,
                  }}>
                    TEAM · {String(idx + 1).padStart(2, "0")}
                  </div>
                  <div style={{
                    fontFamily: "var(--at-font-sans)",
                    fontSize: 18,
                    fontWeight: 500,
                    color: "var(--at-amber)",
                    letterSpacing: "-0.01em",
                  }}>
                    {team.name}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 12 }}>
                  <button className="at-link" onClick={() => openEdit(team)}>✎ EDIT</button>
                  <Popconfirm title="确定删除?" onConfirm={() => handleDelete(team.name)}>
                    <button className="at-link" style={{ color: "var(--at-red)" }}>✕ DEL</button>
                  </Popconfirm>
                </div>
              </div>

              {/* 描述 */}
              <div style={{
                fontFamily: "var(--at-font-sans)",
                fontSize: 14,
                color: "var(--at-text-dim)",
                marginBottom: 14,
                minHeight: 20,
                lineHeight: 1.5,
              }}>
                {team.description || <span style={{ color: "var(--at-text-faint)", fontStyle: "italic" }}>无描述</span>}
              </div>

              {/* 指标 */}
              <div style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: 10,
                padding: "12px 0",
                borderTop: "1px solid var(--at-border-soft)",
                borderBottom: "1px solid var(--at-border-soft)",
                marginBottom: 14,
              }}>
                <div>
                  <div className="at-stat-label" style={{ marginBottom: 4 }}>Root</div>
                  <div style={{ fontFamily: "var(--at-font-mono)", fontSize: 13, color: "var(--at-text)" }}>
                    {team.root.role}
                  </div>
                </div>
                <div>
                  <div className="at-stat-label" style={{ marginBottom: 4 }}>Workers</div>
                  <div style={{ fontFamily: "var(--at-font-mono)", fontSize: 16, color: "var(--at-green)", fontWeight: 700 }}>
                    {workerCount}
                    {hasSubteam && <span style={{ color: "var(--at-text-faint)", fontSize: 12 }}> +sub</span>}
                  </div>
                </div>
              </div>

              {/* skills */}
              {team.skills?.length > 0 && (
                <div style={{ marginBottom: 12 }}>
                  <div className="at-stat-label" style={{ marginBottom: 6 }}>Skills</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                    {team.skills.map((s) => (
                      <span key={s} className="at-tag" style={{ color: "var(--at-blue)" }}>{s}</span>
                    ))}
                  </div>
                </div>
              )}

              {/* worker 列表:点击成员行打开明细抽屉 */}
              <div style={{ marginBottom: 14 }}>
                <div className="at-stat-label" style={{ marginBottom: 6 }}>Members</div>
                <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                  {workers.slice(0, 5).map((w, i) => (
                    <div
                      key={i}
                      onClick={() => setDrawerTeam(team.name)}
                      title="查看成员明细与运行情况"
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        fontFamily: "var(--at-font-mono)",
                        fontSize: 13,
                        padding: "3px 6px",
                        margin: "0 -6px",
                        borderRadius: 2,
                        cursor: "pointer",
                      }}
                      onMouseEnter={(e) => { e.currentTarget.style.background = "var(--at-bg-deep)"; }}
                      onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                    >
                      <span style={{ color: "var(--at-text-faint)", width: 20 }}>
                        {String(i + 1).padStart(2, "0")}
                      </span>
                      <span style={{ color: "var(--at-text)" }}>{w.name}</span>
                      <span style={{
                        color: w.role === "subteam" ? "var(--at-blue)" : "var(--at-green)",
                        fontSize: 12,
                        letterSpacing: "0.1em",
                        textTransform: "uppercase",
                      }}>
                        {w.role}
                      </span>
                    </div>
                  ))}
                  {workers.length > 5 && (
                    <button
                      className="at-link"
                      style={{ marginTop: 4, fontFamily: "var(--at-font-mono)" }}
                      onClick={() => setDrawerTeam(team.name)}
                    >
                      + {workers.length - 5} more · 查看全部成员明细
                    </button>
                  )}
                </div>
              </div>

              {/* agent 树折叠 */}
              <details style={{ borderTop: "1px solid var(--at-border-soft)", paddingTop: 10 }}>
                <summary style={{
                  cursor: "pointer",
                  fontFamily: "var(--at-font-mono)",
                  fontSize: 12,
                  color: "var(--at-text-faint)",
                  letterSpacing: "0.12em",
                  textTransform: "uppercase",
                }}>
                  ▾ Agent Tree
                </summary>
                <pre style={{
                  fontSize: 13,
                  marginTop: 8,
                  padding: 10,
                  background: "var(--at-bg-deep)",
                  border: "1px solid var(--at-border)",
                  color: "var(--at-text-mono)",
                  whiteSpace: "pre-wrap",
                  lineHeight: 1.5,
                }}>
                  {renderAgentTree(team.root)}
                </pre>
              </details>

              {/* Evolution */}
              {workerCount > 0 && (
                <details style={{ marginTop: 10 }}>
                  <summary style={{
                    cursor: "pointer",
                    fontFamily: "var(--at-font-mono)",
                    fontSize: 12,
                    color: "var(--at-amber)",
                    letterSpacing: "0.12em",
                    textTransform: "uppercase",
                  }}>
                    ▾ Evolution History
                  </summary>
                  <div style={{ marginTop: 10 }}>
                    <Tabs
                      size="small"
                      items={workers
                        .filter((w) => w.role !== "subteam")
                        .map((w) => ({
                          key: w.name,
                          label: w.name,
                          children: <EvolutionHistory agentName={w.name} onRollback={refetch} />,
                        }))}
                    />
                  </div>
                </details>
              )}

              {/* 记忆层: 团队跨 run 记忆 */}
              <details style={{ marginTop: 10 }}>
                <summary style={{
                  cursor: "pointer",
                  fontFamily: "var(--at-font-mono)",
                  fontSize: 12,
                  color: "var(--at-amber)",
                  letterSpacing: "0.12em",
                  textTransform: "uppercase",
                }}>
                  ▾ Team Memory
                </summary>
                <div style={{ marginTop: 10 }}>
                  <TeamMemory teamName={team.name} />
                </div>
              </details>
            </div>
          );
        })}
      </div>

      {/* 新建/编辑团队表单 */}
      <TeamFormModal
        open={formOpen}
        editing={editingTeam}
        onClose={() => { setFormOpen(false); setEditingTeam(null); }}
        onSuccess={handleFormSuccess}
      />

      {/* 成员明细 + 成员情况抽屉 */}
      <MemberDetailDrawer
        teamName={drawerTeam}
        onClose={() => setDrawerTeam(null)}
      />
    </div>
  );
}
