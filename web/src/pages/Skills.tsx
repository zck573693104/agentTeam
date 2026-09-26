import { useState } from "react";
import { useFetch } from "../hooks/useFetch";
import { api, type SkillItem, type SkillDetail } from "../api/client";
import { PageHeader } from "./Runs";

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

  const skills = data?.skills || [];

  return (
    <div>
      <PageHeader
        index="03 · SKILLS"
        title="技能库"
        subtitle="Capability Library"
        action={<button className="at-btn-amber" onClick={() => refetch()}>↻ Refresh</button>}
      />

      {error && !data && (
        <div style={{ padding: 40, color: "var(--at-red)", fontFamily: "var(--at-font-mono)", fontSize: 12 }}>
          ✕ SIGNAL ERROR: {error}
        </div>
      )}

      {loading && !data && (
        <div style={{ padding: 40, color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 12, textAlign: "center" }}>
          <span style={{ color: "var(--at-amber)" }}>●</span> LOADING SKILLS...
        </div>
      )}

      {data && skills.length === 0 && (
        <div className="at-panel at-fade-in" style={{
          padding: 56,
          textAlign: "center",
          color: "var(--at-text-faint)",
          fontFamily: "var(--at-font-mono)",
          fontSize: 12,
        }}>
          ◌ NO SKILLS · 暂无技能
        </div>
      )}

      {skills.length > 0 && (
        <div className="at-panel at-fade-in" style={{ padding: 0 }}>
          <div style={{
            display: "grid",
            gridTemplateColumns: "60px 1fr 100px 80px",
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
            <span>#</span>
            <span>Name</span>
            <span>Type</span>
            <span style={{ textAlign: "right" }}>Action</span>
          </div>
          {skills.map((s, idx) => {
            const isAuto = s.name.startsWith("auto_");
            const expanded = expandedName === s.name;
            return (
              <div key={s.name}>
                <div
                  onClick={() => handleExpand(s.name)}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "60px 1fr 100px 80px",
                    gap: 16,
                    padding: "12px 20px",
                    borderBottom: expanded ? "none" : "1px solid var(--at-border-soft)",
                    cursor: "pointer",
                    transition: "background 0.12s ease, box-shadow 0.12s ease",
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
                  <span style={{ fontFamily: "var(--at-font-mono)", fontSize: 11, color: "var(--at-text-faint)" }}>
                    {String(idx + 1).padStart(2, "0")}
                  </span>
                  <span style={{
                    fontFamily: "var(--at-font-mono)",
                    fontSize: 13,
                    color: expanded ? "var(--at-amber)" : "var(--at-text)",
                    fontWeight: 500,
                  }}>
                    {s.name}
                  </span>
                  <span>
                    <span
                      className="at-tag"
                      style={{
                        color: isAuto ? "var(--at-amber)" : "var(--at-blue)",
                      }}
                    >
                      {isAuto ? "AUTO" : "PRESET"}
                    </span>
                  </span>
                  <span style={{
                    fontFamily: "var(--at-font-mono)",
                    fontSize: 10,
                    color: expanded ? "var(--at-amber)" : "var(--at-text-faint)",
                    textAlign: "right",
                    letterSpacing: "0.1em",
                  }}>
                    {expanded ? "− HIDE" : "+ VIEW"}
                  </span>
                </div>
                {expanded && (
                  <div style={{
                    padding: "0 20px 20px",
                    background: "var(--at-bg-deep)",
                    borderBottom: "1px solid var(--at-border-soft)",
                  }}>
                    <div style={{
                      fontFamily: "var(--at-font-mono)",
                      fontSize: 9,
                      color: "var(--at-amber)",
                      letterSpacing: "0.18em",
                      textTransform: "uppercase",
                      padding: "10px 0",
                      borderBottom: "1px solid var(--at-border-soft)",
                      marginBottom: 10,
                    }}>
                      ▸ SKILL CONTENT
                    </div>
                    {contentLoading ? (
                      <div style={{ padding: 24, textAlign: "center", color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 11 }}>
                        <span style={{ color: "var(--at-amber)" }}>●</span> LOADING...
                      </div>
                    ) : skillContent ? (
                      <pre style={{
                        margin: 0,
                        fontSize: 12,
                        lineHeight: 1.6,
                        maxHeight: 480,
                        overflow: "auto",
                        whiteSpace: "pre-wrap",
                        color: "var(--at-text-mono)",
                      }}>
                        {skillContent.content}
                      </pre>
                    ) : (
                      <div style={{ color: "var(--at-red)", fontFamily: "var(--at-font-mono)", fontSize: 11 }}>
                        ✕ FAILED TO LOAD
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
