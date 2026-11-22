/**
 * V2 记忆库(P4/P5 学习闭环的可视面):
 * - 教训:复盘蒸馏产出(memory/lessons/*.md,人可审可改)
 * - 入站研判校准规则:观察模式人工确认样本蒸馏产物(EvalEngine 注入)
 */
import { useFetch } from "../../hooks/useFetch";
import { PageHeader } from "../../components/PageHeader";

interface Lesson {
  id: string;
  scope: string;
  repo: string;
  condition: string;
  lesson: string;
  created_at: string;
  use_count: string;
  last_used_at: string | null;
}

interface MemoryBody {
  enabled: boolean;
  lessons: Lesson[];
  inbound_rules: string[];
}

function fmtTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("zh-CN", { hour12: false });
}

export default function V2Memory() {
  const { data, loading } = useFetch<MemoryBody>("/api/memory");
  const lessons = data?.lessons || [];
  const rules = data?.inbound_rules || [];

  return (
    <div>
      <PageHeader index="09 · V2 MEMORY" title="记忆库" subtitle="Agent Memory" />
      {loading && !data && (
        <div style={{ padding: 40, textAlign: "center", color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 13 }}>
          <span style={{ color: "var(--at-amber)" }}>●</span> LOADING...
        </div>
      )}
      {data && !data.enabled && (
        <div className="at-panel at-fade-in" style={{ padding: 56, textAlign: "center", color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 13 }}>
          ◌ DISABLED · 记忆库未启用(AGENTTEAM_V2_MEMORY_DIR)
        </div>
      )}
      {data?.enabled && (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {/* 入站研判校准规则 */}
          <div className="at-panel at-panel-corners" style={{ padding: 16 }}>
            <div className="at-stat-label" style={{ marginBottom: 8 }}>
              入站研判校准规则 · {rules.length} 条(观察模式人工确认样本蒸馏)
            </div>
            {rules.length === 0 ? (
              <div style={{ fontSize: 13, color: "var(--at-text-faint)" }}>
                ◌ 暂无 · 人工确认 would_accept 攒够阈值后自动蒸馏
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {rules.map((r, i) => (
                  <div key={i} style={{ fontSize: 13, color: "var(--at-text-dim)" }}>
                    <span style={{ color: "var(--at-cyan)" }}>›</span> {r}
                  </div>
                ))}
              </div>
            )}
          </div>
          {/* 教训卡片 */}
          <div className="at-stat-label">复盘教训 · {lessons.length} 条</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(420px, 1fr))", gap: 14 }}>
            {lessons.map((l) => (
              <div key={l.id} className="at-panel at-panel-corners" style={{ padding: 14 }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                  <span className="at-tag" style={{
                    color: l.scope === "repo" ? "var(--at-blue)" : "var(--at-green)",
                    fontFamily: "var(--at-font-mono)", fontSize: 11,
                  }}>
                    {l.scope === "repo" ? `REPO ${l.repo}` : "GLOBAL"}
                  </span>
                  <span style={{ fontSize: 11, color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)" }}>
                    命中 {l.use_count} · {fmtTime(l.created_at)}
                  </span>
                </div>
                <div style={{ fontSize: 13, marginBottom: 6 }}>
                  <span style={{ color: "var(--at-amber)" }}>[条件]</span>{" "}
                  {l.condition}
                </div>
                <div style={{ fontSize: 13, color: "var(--at-text-dim)" }}>
                  <span style={{ color: "var(--at-green)" }}>[做法]</span>{" "}
                  {l.lesson}
                </div>
              </div>
            ))}
          </div>
          {data.enabled && lessons.length === 0 && (
            <div className="at-panel at-fade-in" style={{ padding: 40, textAlign: "center", color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 13 }}>
              ◌ EMPTY · 尚无复盘教训(工单终态后自动蒸馏)
            </div>
          )}
        </div>
      )}
    </div>
  );
}
