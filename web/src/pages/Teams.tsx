import { useState } from "react";
import {
  Input,
  Modal,
  Popconfirm,
  Tabs,
  message,
} from "antd";
import { useFetch } from "../hooks/useFetch";
import { api, type AgentNode, type Team, type TeamRefNode } from "../api/client";
import EvolutionHistory from "../components/EvolutionHistory";
import { PageHeader } from "./Runs";

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

  return (
    <div>
      <PageHeader
        index="02 · TEAMS"
        title="团队配置"
        subtitle="Agent Squads"
        action={<button className="at-btn-amber" onClick={() => setModalOpen(true)}>+ 注册团队</button>}
      />

      {error && (
        <div style={{ padding: 24, color: "var(--at-red)", fontFamily: "var(--at-font-mono)", fontSize: 12 }}>
          ✕ SIGNAL ERROR: {error}
        </div>
      )}

      {loading && !data && (
        <div style={{ padding: 40, color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 12, textAlign: "center" }}>
          <span style={{ color: "var(--at-amber)" }}>●</span> LOADING TEAMS...
        </div>
      )}

      {data && data.length === 0 && (
        <div className="at-panel at-fade-in" style={{
          padding: 56,
          textAlign: "center",
          color: "var(--at-text-faint)",
          fontFamily: "var(--at-font-mono)",
          fontSize: 12,
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
              {/* 卡片头:名称 + 删除 */}
              <div style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "flex-start",
                marginBottom: 14,
              }}>
                <div>
                  <div style={{
                    fontFamily: "var(--at-font-mono)",
                    fontSize: 9,
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
                <Popconfirm title="确定删除?" onConfirm={() => handleDelete(team.name)}>
                  <button className="at-link" style={{ color: "var(--at-red)" }}>✕ DEL</button>
                </Popconfirm>
              </div>

              {/* 描述 */}
              <div style={{
                fontFamily: "var(--at-font-sans)",
                fontSize: 13,
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
                  <div style={{ fontFamily: "var(--at-font-mono)", fontSize: 12, color: "var(--at-text)" }}>
                    {team.root.role}
                  </div>
                </div>
                <div>
                  <div className="at-stat-label" style={{ marginBottom: 4 }}>Workers</div>
                  <div style={{ fontFamily: "var(--at-font-mono)", fontSize: 16, color: "var(--at-green)", fontWeight: 700 }}>
                    {workerCount}
                    {hasSubteam && <span style={{ color: "var(--at-text-faint)", fontSize: 10 }}> +sub</span>}
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

              {/* worker 列表 */}
              <div style={{ marginBottom: 14 }}>
                <div className="at-stat-label" style={{ marginBottom: 6 }}>Members</div>
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  {workers.slice(0, 5).map((w, i) => (
                    <div key={i} style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      fontFamily: "var(--at-font-mono)",
                      fontSize: 11,
                    }}>
                      <span style={{ color: "var(--at-text-faint)", width: 18 }}>
                        {String(i + 1).padStart(2, "0")}
                      </span>
                      <span style={{ color: "var(--at-text)" }}>{w.name}</span>
                      <span style={{
                        color: w.role === "subteam" ? "var(--at-blue)" : "var(--at-green)",
                        fontSize: 9,
                        letterSpacing: "0.1em",
                        textTransform: "uppercase",
                      }}>
                        {w.role}
                      </span>
                    </div>
                  ))}
                  {workers.length > 5 && (
                    <div style={{ color: "var(--at-text-faint)", fontSize: 10, fontFamily: "var(--at-font-mono)", marginTop: 2 }}>
                      + {workers.length - 5} more
                    </div>
                  )}
                </div>
              </div>

              {/* agent 树折叠 */}
              <details style={{ borderTop: "1px solid var(--at-border-soft)", paddingTop: 10 }}>
                <summary style={{
                  cursor: "pointer",
                  fontFamily: "var(--at-font-mono)",
                  fontSize: 10,
                  color: "var(--at-text-faint)",
                  letterSpacing: "0.12em",
                  textTransform: "uppercase",
                }}>
                  ▾ Agent Tree
                </summary>
                <pre style={{
                  fontSize: 11,
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
                    fontSize: 10,
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
            </div>
          );
        })}
      </div>

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
