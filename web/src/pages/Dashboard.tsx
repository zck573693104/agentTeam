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
  cancelling: "取消中",
  cancelled: "已取消",
};

/** 数字加千位分隔 */
function fmtNum(n: number): string {
  return n.toLocaleString("en-US");
}

interface StatCardProps {
  label: string;
  value: number;
  hint?: string;
  accentColor?: string;
  glow?: boolean;
  delayClass?: string;
}

function StatCard({ label, value, hint, accentColor, glow, delayClass }: StatCardProps) {
  return (
    <div
      className={`at-panel at-panel-corners at-fade-in ${delayClass ?? ""}`}
      style={{
        padding: "20px 22px",
        position: "relative",
        boxShadow: glow && accentColor ? `0 0 24px ${accentColor}22` : undefined,
      }}
    >
      <div style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "flex-start",
        marginBottom: 14,
      }}>
        <span className="at-stat-label">{label}</span>
        {accentColor && (
          <span style={{
            width: 6,
            height: 6,
            background: accentColor,
            boxShadow: `0 0 8px ${accentColor}`,
          }} />
        )}
      </div>
      <div className="at-stat-value">{fmtNum(value)}</div>
      {hint && (
        <div style={{
          fontFamily: "var(--at-font-mono)",
          fontSize: 10,
          color: "var(--at-text-faint)",
          letterSpacing: "0.1em",
          marginTop: 8,
          textTransform: "uppercase",
        }}>
          {hint}
        </div>
      )}
    </div>
  );
}

export default function Dashboard() {
  const { data, loading, error, refetch } = useFetch<DashboardData>("/api/dashboard");
  const navigate = useNavigate();

  if (loading && !data) {
    return (
      <div style={{ padding: 40, color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 12 }}>
        <span style={{ color: "var(--at-amber)" }}>●</span> INITIALIZING TELEMETRY...
      </div>
    );
  }
  if (error && !data) {
    return (
      <div style={{ padding: 40, color: "var(--at-red)", fontFamily: "var(--at-font-mono)", fontSize: 12 }}>
        ✕ SIGNAL ERROR: {error}
      </div>
    );
  }
  if (!data) return null;

  const statusData = Object.entries(data.by_status).map(([k, v]) => ({
    type: STATUS_LABELS[k] || k,
    rawKey: k,
    value: v,
  }));

  const teamData = Object.entries(data.by_team)
    .map(([k, v]) => ({ team: k, count: v }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 6);

  const total = statusData.reduce((s, x) => s + x.value, 0) || 1;
  const runningCount = data.by_status.running || 0;
  const interruptedCount = data.by_status.interrupted || 0;

  return (
    <div>
      {/* 页头 */}
      <header className="at-fade-in" style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "flex-end",
        marginBottom: 28,
        paddingBottom: 18,
        borderBottom: "1px solid var(--at-border)",
      }}>
        <div>
          <div style={{
            fontFamily: "var(--at-font-mono)",
            fontSize: 10,
            color: "var(--at-amber)",
            letterSpacing: "0.24em",
            textTransform: "uppercase",
            marginBottom: 6,
          }}>
            // 01 · OVERVIEW
          </div>
          <h1 style={{
            fontFamily: "var(--at-font-sans)",
            fontSize: 28,
            fontWeight: 300,
            color: "var(--at-text)",
            margin: 0,
            letterSpacing: "-0.01em",
          }}>
            全局态势 <span style={{ color: "var(--at-text-faint)", fontWeight: 300 }}>· Mission Control</span>
          </h1>
          <div style={{
            fontFamily: "var(--at-font-mono)",
            fontSize: 11,
            color: "var(--at-text-faint)",
            marginTop: 6,
          }}>
            {new Date().toLocaleString("zh-CN", { hour12: false })} · 监控中 {runningCount} 个运行 · {interruptedCount} 待审批
          </div>
        </div>
        <button className="at-btn-amber" onClick={() => refetch()}>
          ↻ Refresh
        </button>
      </header>

      {/* 统计卡片 */}
      <section style={{
        display: "grid",
        gridTemplateColumns: "repeat(4, 1fr)",
        gap: 14,
        marginBottom: 24,
      }}>
        <StatCard
          label="Total Runs"
          value={data.total_runs}
          hint="累计执行"
          accentColor="#ffb547"
          delayClass="at-fade-in-1"
        />
        <StatCard
          label="Total Tokens"
          value={data.total_tokens}
          hint="累计消耗"
          accentColor="#4ad8e8"
          delayClass="at-fade-in-2"
        />
        <StatCard
          label="Running"
          value={runningCount}
          hint={runningCount > 0 ? "● 实时执行中" : "空闲"}
          accentColor={runningCount > 0 ? "#5dff9e" : undefined}
          glow={runningCount > 0}
          delayClass="at-fade-in-3"
        />
        <StatCard
          label="Completed"
          value={data.by_status.completed || 0}
          hint="成功完成"
          accentColor="#ffd24a"
          delayClass="at-fade-in-4"
        />
      </section>

      {/* 状态分布 + 团队分布 */}
      <section style={{
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        gap: 14,
        marginBottom: 24,
      }}>
        {/* 状态分布:不用 antd Pie,改用条形堆叠 */}
        <div className="at-panel at-fade-in at-fade-in-1" style={{ padding: 20 }}>
          <div className="at-section-title">Status Distribution · 状态分布</div>
          {statusData.length === 0 ? (
            <div style={{ color: "var(--at-text-faint)", padding: 24, textAlign: "center", fontFamily: "var(--at-font-mono)", fontSize: 12 }}>
              NO DATA
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {/* 顶部堆叠条 */}
              <div style={{
                display: "flex",
                height: 6,
                background: "var(--at-bg-deep)",
                borderRadius: 1,
                overflow: "hidden",
              }}>
                {statusData.map((s) => {
                  const colorMap: Record<string, string> = {
                    pending: "#6b7785",
                    running: "#5dff9e",
                    completed: "#ffd24a",
                    failed: "#ff5a5f",
                    interrupted: "#ffb547",
                    cancelling: "#ffb547",
                    cancelled: "#6b7785",
                  };
                  return (
                    <div
                      key={s.rawKey}
                      style={{
                        width: `${(s.value / total) * 100}%`,
                        background: colorMap[s.rawKey] || "#6b7785",
                        opacity: 0.9,
                      }}
                      title={`${s.type}: ${s.value}`}
                    />
                  );
                })}
              </div>
              {/* 图例 */}
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {statusData.map((s) => {
                  const colorMap: Record<string, string> = {
                    pending: "#6b7785",
                    running: "#5dff9e",
                    completed: "#ffd24a",
                    failed: "#ff5a5f",
                    interrupted: "#ffb547",
                    cancelling: "#ffb547",
                    cancelled: "#6b7785",
                  };
                  const pct = ((s.value / total) * 100).toFixed(1);
                  return (
                    <div key={s.rawKey} style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 10,
                      fontFamily: "var(--at-font-mono)",
                      fontSize: 12,
                    }}>
                      <span style={{
                        width: 8,
                        height: 8,
                        background: colorMap[s.rawKey] || "#6b7785",
                      }} />
                      <span style={{ color: "var(--at-text)", width: 80, fontSize: 11, letterSpacing: "0.08em", textTransform: "uppercase" }}>
                        {s.type}
                      </span>
                      <span style={{ color: "var(--at-text)", fontWeight: 600, width: 50, textAlign: "right" }}>
                        {s.value}
                      </span>
                      <span style={{ color: "var(--at-text-faint)", fontSize: 10, width: 50, textAlign: "right" }}>
                        {pct}%
                      </span>
                      {/* 横向比例条 */}
                      <div style={{
                        flex: 1,
                        height: 2,
                        background: "var(--at-bg-deep)",
                        position: "relative",
                      }}>
                        <div style={{
                          position: "absolute",
                          left: 0, top: 0, bottom: 0,
                          width: `${pct}%`,
                          background: colorMap[s.rawKey] || "#6b7785",
                        }} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {/* 团队分布:横向条形图,纯 CSS */}
        <div className="at-panel at-fade-in at-fade-in-2" style={{ padding: 20 }}>
          <div className="at-section-title">Team Activity · 团队活跃度</div>
          {teamData.length === 0 ? (
            <div style={{ color: "var(--at-text-faint)", padding: 24, textAlign: "center", fontFamily: "var(--at-font-mono)", fontSize: 12 }}>
              NO TEAMS
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {(() => {
                const max = Math.max(...teamData.map((t) => t.count), 1);
                return teamData.map((t, i) => (
                  <div key={t.team} style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 12,
                  }}>
                    <span style={{
                      fontFamily: "var(--at-font-mono)",
                      fontSize: 10,
                      color: "var(--at-text-faint)",
                      width: 18,
                    }}>
                      {String(i + 1).padStart(2, "0")}
                    </span>
                    <span style={{
                      fontFamily: "var(--at-font-mono)",
                      fontSize: 12,
                      color: "var(--at-text)",
                      width: 100,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}>
                      {t.team}
                    </span>
                    <div style={{
                      flex: 1,
                      height: 14,
                      background: "var(--at-bg-deep)",
                      position: "relative",
                      border: "1px solid var(--at-border-soft)",
                    }}>
                      <div style={{
                        position: "absolute",
                        left: 0, top: 0, bottom: 0,
                        width: `${(t.count / max) * 100}%`,
                        background: "linear-gradient(90deg, var(--at-amber-dim), var(--at-amber))",
                        transition: "width 0.5s ease",
                      }} />
                      <span style={{
                        position: "absolute",
                        right: 8,
                        top: "50%",
                        transform: "translateY(-50%)",
                        fontFamily: "var(--at-font-mono)",
                        fontSize: 10,
                        color: "var(--at-text)",
                        fontWeight: 600,
                        mixBlendMode: "difference",
                      }}>
                        {t.count}
                      </span>
                    </div>
                  </div>
                ));
              })()}
            </div>
          )}
        </div>
      </section>

      {/* 最近 Run */}
      <section className="at-panel at-fade-in at-fade-in-3" style={{ padding: 0 }}>
        <header style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "14px 20px",
          borderBottom: "1px solid var(--at-border)",
        }}>
          <div className="at-section-title" style={{ margin: 0 }}>
            Recent Runs · 最近执行
          </div>
          <button className="at-link" onClick={() => refetch()}>
            ↻ REFRESH
          </button>
        </header>

        {data.recent_runs.length === 0 ? (
          <div style={{
            padding: 48,
            textAlign: "center",
            color: "var(--at-text-faint)",
            fontFamily: "var(--at-font-mono)",
            fontSize: 12,
          }}>
            ◌ NO RUNS YET · 暂无执行记录
          </div>
        ) : (
          <div>
            {/* 表头 */}
            <div style={{
              display: "grid",
              gridTemplateColumns: "120px 1fr 140px 100px 160px",
              gap: 16,
              padding: "10px 20px",
              background: "var(--at-bg-elev)",
              fontFamily: "var(--at-font-mono)",
              fontSize: 10,
              color: "var(--at-text-faint)",
              letterSpacing: "0.14em",
              textTransform: "uppercase",
              borderBottom: "1px solid var(--at-border)",
            }}>
              <span>Run ID</span>
              <span>Task</span>
              <span>Team</span>
              <span>Status</span>
              <span style={{ textAlign: "right" }}>Created</span>
            </div>
            {/* 行 */}
            {data.recent_runs.map((r: Run, idx: number) => (
              <div
                key={r.run_id}
                onClick={() => navigate(`/runs/${r.run_id}`)}
                style={{
                  display: "grid",
                  gridTemplateColumns: "120px 1fr 140px 100px 160px",
                  gap: 16,
                  padding: "12px 20px",
                  borderBottom: idx === data.recent_runs.length - 1 ? "none" : "1px solid var(--at-border-soft)",
                  cursor: "pointer",
                  transition: "background 0.12s ease, box-shadow 0.12s ease",
                  position: "relative",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = "var(--at-bg-hover)";
                  e.currentTarget.style.boxShadow = "inset 2px 0 0 var(--at-amber)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = "transparent";
                  e.currentTarget.style.boxShadow = "none";
                }}
              >
                <span style={{
                  fontFamily: "var(--at-font-mono)",
                  fontSize: 11,
                  color: "var(--at-amber)",
                }}>
                  {r.run_id.slice(0, 8)}
                </span>
                <span style={{
                  fontSize: 13,
                  color: "var(--at-text)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}>
                  {r.task}
                </span>
                <span style={{
                  fontFamily: "var(--at-font-mono)",
                  fontSize: 11,
                  color: "var(--at-text-dim)",
                }}>
                  {r.team_name}
                </span>
                <span>
                  <StatusBadge status={r.status} />
                </span>
                <span style={{
                  fontFamily: "var(--at-font-mono)",
                  fontSize: 10,
                  color: "var(--at-text-faint)",
                  textAlign: "right",
                }}>
                  {new Date(r.created_at).toLocaleString("zh-CN", { hour12: false })}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
