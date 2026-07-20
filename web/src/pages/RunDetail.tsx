import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Input,
  Modal,
  message,
} from "antd";
import { useFetch } from "../hooks/useFetch";
import { api, type Approval, type Run } from "../api/client";
import StatusBadge from "../components/StatusBadge";
import SSEViewer from "../components/SSEViewer";
import { PageHeader } from "./Runs";

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
  const [activeTab, setActiveTab] = useState<"trace" | "approvals">("trace");
  const [rejectModalOpen, setRejectModalOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [submitting, setSubmitting] = useState(false);

  if (loading && (!run || run.run_id !== runId)) {
    return (
      <div style={{ padding: 40, color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 12 }}>
        <span style={{ color: "var(--at-amber)" }}>●</span> LOADING RUN...
      </div>
    );
  }
  if (error && (!run || run.run_id !== runId)) {
    return (
      <div style={{ padding: 40, color: "var(--at-red)", fontFamily: "var(--at-font-mono)", fontSize: 12 }}>
        ✕ SIGNAL ERROR: {error}
      </div>
    );
  }
  if (!run) {
    return (
      <div style={{ padding: 40, color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 12 }}>
        ✕ RUN NOT FOUND
      </div>
    );
  }

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
      setSseKey((k) => k + 1);
      return true;
    } catch (e) {
      message.error((e as Error).message);
      return false;
    } finally {
      setSubmitting(false);
    }
  };

  // Run 状态徽章 + 警示
  const isInterrupted = run.status === "interrupted";
  const isRunning = run.status === "running";

  return (
    <div>
      {/* 面包屑 */}
      <div className="at-fade-in" style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        marginBottom: 14,
        fontFamily: "var(--at-font-mono)",
        fontSize: 10,
        color: "var(--at-text-faint)",
        letterSpacing: "0.1em",
        textTransform: "uppercase",
      }}>
        <button
          onClick={() => navigate("/runs")}
          style={{
            background: "transparent",
            border: "none",
            color: "var(--at-amber)",
            cursor: "pointer",
            fontFamily: "inherit",
            fontSize: "inherit",
            letterSpacing: "inherit",
            textTransform: "inherit",
            padding: 0,
          }}
        >
          ← RUNS
        </button>
        <span>/</span>
        <span style={{ color: "var(--at-text-dim)" }}>{run.run_id.slice(0, 8)}</span>
      </div>

      <PageHeader
        index="04 · RUN DETAIL"
        title={`Run ${run.run_id.slice(0, 8)}`}
        subtitle={run.team_name}
        action={
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <StatusBadge status={run.status} />
          </div>
        }
      />

      {/* 中断态警示条 */}
      {isInterrupted && (
        <div className="at-fade-in" style={{
          background: "rgba(255, 181, 71, 0.08)",
          border: "1px solid var(--at-amber)",
          borderLeft: "3px solid var(--at-amber)",
          padding: "12px 18px",
          marginBottom: 16,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          fontFamily: "var(--at-font-mono)",
          fontSize: 12,
        }}>
          <div>
            <span style={{ color: "var(--at-amber)", fontWeight: 600, letterSpacing: "0.1em" }}>
              ⚠ APPROVAL REQUIRED
            </span>
            <span style={{ color: "var(--at-text-dim)", marginLeft: 12 }}>
              该 Run 等待审批决策
            </span>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              className="at-btn-amber"
              disabled={submitting}
              onClick={() => handleApprove(true)}
            >
              ✓ Approve
            </button>
            <button
              className="at-btn-amber"
              style={{ borderColor: "var(--at-red)", color: "var(--at-red)" }}
              disabled={submitting}
              onClick={() => setRejectModalOpen(true)}
            >
              ✕ Reject
            </button>
          </div>
        </div>
      )}

      {/* 元数据:KPI 卡片 */}
      <section className="at-fade-in" style={{
        display: "grid",
        gridTemplateColumns: "repeat(4, 1fr)",
        gap: 14,
        marginBottom: 16,
      }}>
        <div className={`at-panel at-panel-corners ${isRunning ? "at-scanline" : ""}`} style={{ padding: 18 }}>
          <div className="at-stat-label">Status</div>
          <div style={{ marginTop: 6 }}><StatusBadge status={run.status} /></div>
        </div>
        <div className="at-panel at-panel-corners" style={{ padding: 18 }}>
          <div className="at-stat-label">Tokens</div>
          <div className="at-stat-value" style={{ fontSize: 22, marginTop: 6 }}>
            {run.total_tokens.toLocaleString("en-US")}
          </div>
        </div>
        <div className="at-panel at-panel-corners" style={{ padding: 18 }}>
          <div className="at-stat-label">Created</div>
          <div style={{
            fontFamily: "var(--at-font-mono)",
            fontSize: 12,
            color: "var(--at-text)",
            marginTop: 6,
          }}>
            {new Date(run.created_at).toLocaleString("zh-CN", { hour12: false })}
          </div>
        </div>
        <div className="at-panel at-panel-corners" style={{ padding: 18 }}>
          <div className="at-stat-label">Ended</div>
          <div style={{
            fontFamily: "var(--at-font-mono)",
            fontSize: 12,
            color: run.ended_at ? "var(--at-text)" : "var(--at-text-faint)",
            marginTop: 6,
          }}>
            {run.ended_at
              ? new Date(run.ended_at).toLocaleString("zh-CN", { hour12: false })
              : "— PENDING —"}
          </div>
        </div>
      </section>

      {/* 任务详情 */}
      <section className="at-panel at-fade-in" style={{ padding: 18, marginBottom: 16 }}>
        <div className="at-section-title">Task · 任务描述</div>
        <div style={{
          fontFamily: "var(--at-font-sans)",
          fontSize: 14,
          color: "var(--at-text)",
          lineHeight: 1.6,
          padding: 12,
          background: "var(--at-bg-deep)",
          border: "1px solid var(--at-border-soft)",
          borderLeft: "2px solid var(--at-amber)",
        }}>
          {run.task}
        </div>
      </section>

      {/* Tab 切换:Trace / Approvals */}
      <section className="at-panel at-fade-in" style={{ padding: 0 }}>
        <div style={{
          display: "flex",
          borderBottom: "1px solid var(--at-border)",
        }}>
          {(["trace", "approvals"] as const).map((tab) => {
            const active = activeTab === tab;
            return (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                style={{
                  background: "transparent",
                  border: "none",
                  borderBottom: active ? "2px solid var(--at-amber)" : "2px solid transparent",
                  color: active ? "var(--at-amber)" : "var(--at-text-dim)",
                  padding: "14px 22px",
                  cursor: "pointer",
                  fontFamily: "var(--at-font-mono)",
                  fontSize: 11,
                  fontWeight: 600,
                  letterSpacing: "0.16em",
                  textTransform: "uppercase",
                  transition: "all 0.15s ease",
                }}
              >
                {tab === "trace" ? "▸ Live Trace" : `▸ Approvals${approvals && approvals.length > 0 ? ` (${approvals.length})` : ""}`}
              </button>
            );
          })}
        </div>

        <div style={{ padding: 16 }}>
          {activeTab === "trace" ? (
            <SSEViewer
              runId={run.run_id}
              refreshKey={sseKey}
              onReconnect={() => setSseKey((k) => k + 1)}
            />
          ) : (
            <div>
              {(approvals || []).length === 0 ? (
                <div style={{
                  padding: 48,
                  textAlign: "center",
                  color: "var(--at-text-faint)",
                  fontFamily: "var(--at-font-mono)",
                  fontSize: 12,
                }}>
                  ◌ NO APPROVAL RECORDS
                </div>
              ) : (
                <div>
                  {(approvals || []).map((a, idx) => (
                    <div
                      key={a.id}
                      style={{
                        display: "grid",
                        gridTemplateColumns: "100px 120px 1fr 140px 100px",
                        gap: 12,
                        padding: "12px 8px",
                        borderBottom: idx === (approvals || []).length - 1 ? "none" : "1px solid var(--at-border-soft)",
                        fontFamily: "var(--at-font-mono)",
                        fontSize: 11,
                      }}
                    >
                      <span style={{ color: "var(--at-amber)" }}>{a.id.slice(0, 8)}</span>
                      <span>
                        <span
                          className="at-tag"
                          style={{
                            color: a.status === "approved" ? "var(--at-green)" :
                                   a.status === "rejected" ? "var(--at-red)" :
                                   "var(--at-amber)",
                          }}
                        >
                          {a.status?.toUpperCase() || "PENDING"}
                        </span>
                      </span>
                      <span style={{ color: "var(--at-text-dim)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {a.reason || "—"}
                      </span>
                      <span style={{ color: "var(--at-text-faint)" }}>
                        {a.decided_at ? new Date(a.decided_at).toLocaleString("zh-CN", { hour12: false }) : "—"}
                      </span>
                      <span style={{ color: "var(--at-text-faint)", textAlign: "right" }}>
                        {a.decider || "—"}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </section>

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
          placeholder="输入拒绝原因(可选)"
          style={{ marginTop: 12 }}
        />
      </Modal>
    </div>
  );
}
