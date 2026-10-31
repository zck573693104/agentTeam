import { Popconfirm } from "antd";
import { useFetch } from "../hooks/useFetch";
import { api, type TeamMemory } from "../api/client";

/** 团队跨 run 记忆列表(记忆层):查看 + 删除(记忆卫生)。 */
export default function TeamMemory({
  teamName,
  onDeleted,
}: {
  teamName: string;
  onDeleted?: () => void;
}) {
  const { data, loading, error, refetch } = useFetch<TeamMemory[]>(
    `/api/teams/${teamName}/memories`
  );

  const handleDelete = async (id: number) => {
    try {
      await api(`/api/teams/${teamName}/memories/${id}`, { method: "DELETE" });
      refetch();
      onDeleted?.();
    } catch {
      // 删除失败静默(列表下次打开仍可见)
    }
  };

  if (loading && !data) {
    return (
      <div style={{
        padding: 16,
        color: "var(--at-text-faint)",
        fontFamily: "var(--at-font-mono)",
        fontSize: 11,
      }}>
        LOADING MEMORIES...
      </div>
    );
  }
  if (error) {
    return (
      <div style={{
        padding: 16,
        color: "var(--at-red)",
        fontFamily: "var(--at-font-mono)",
        fontSize: 11,
      }}>
        ✕ {error}
      </div>
    );
  }

  const memories = data || [];
  if (memories.length === 0) {
    return (
      <div style={{
        padding: "20px 16px",
        color: "var(--at-text-faint)",
        fontFamily: "var(--at-font-mono)",
        fontSize: 11,
        textAlign: "center",
      }}>
        ◌ 暂无记忆 · run 完成后自动蒸馏团队经验
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {memories.map((m) => (
        <div key={m.id} style={{
          padding: "10px 12px",
          background: "var(--at-bg-deep)",
          border: "1px solid var(--at-border)",
          borderLeft: `2px solid ${m.outcome === "completed" ? "var(--at-green)" : "var(--at-red)"}`,
        }}>
          <div style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: 6,
            fontFamily: "var(--at-font-mono)",
            fontSize: 10,
            color: "var(--at-text-faint)",
          }}>
            <span>
              <span style={{ color: m.outcome === "completed" ? "var(--at-green)" : "var(--at-red)" }}>
                {m.outcome === "completed" ? "✓" : "✕"}
              </span>
              {" "}
              {(m.created_at || "").replace("T", " ").slice(0, 16)}
              <span style={{ marginLeft: 8, color: "var(--at-text-dim)" }}>
                run {m.run_id?.slice(0, 8)}
              </span>
            </span>
            <Popconfirm title="删除这条记忆?" onConfirm={() => handleDelete(m.id)}>
              <button className="at-link" style={{ color: "var(--at-red)", fontSize: 10 }}>✕</button>
            </Popconfirm>
          </div>
          <div style={{
            fontFamily: "var(--at-font-sans)",
            fontSize: 12,
            color: "var(--at-text)",
            lineHeight: 1.5,
          }}>
            {m.summary}
          </div>
          {m.meta?.key_points && m.meta.key_points.length > 0 && (
            <ul style={{
              margin: "6px 0 0",
              paddingLeft: 18,
              fontFamily: "var(--at-font-sans)",
              fontSize: 11,
              color: "var(--at-text-dim)",
              lineHeight: 1.6,
            }}>
              {m.meta.key_points.map((p, i) => (
                <li key={i}>{p}</li>
              ))}
            </ul>
          )}
          {m.meta?.files && m.meta.files.length > 0 && (
            <div style={{
              marginTop: 6,
              display: "flex",
              flexWrap: "wrap",
              gap: 4,
            }}>
              {m.meta.files.map((f, i) => (
                <span key={i} className="at-tag" style={{ color: "var(--at-blue)", fontSize: 10 }}>
                  {f}
                </span>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
