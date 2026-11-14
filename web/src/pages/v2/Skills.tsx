/** V2 技能库:skills/ 目录 .md 文件(worker.skills 引用)。 */
import { useFetch } from "../../hooks/useFetch";
import { PageHeader } from "../../components/PageHeader";

export default function V2Skills() {
  const { data, loading } = useFetch<{ skills: string[] }>("/api/skills");
  return (
    <div>
      <PageHeader index="08 · V2 SKILLS" title="技能库" subtitle="Skills" />
      {loading && !data && (
        <div style={{ padding: 40, textAlign: "center", color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 13 }}>
          <span style={{ color: "var(--at-amber)" }}>●</span> LOADING...
        </div>
      )}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        {(data?.skills || []).map((s) => (
          <span key={s} className="at-panel" style={{ padding: "8px 14px", fontFamily: "var(--at-font-mono)", fontSize: 13, color: "var(--at-blue)" }}>
            {s}
          </span>
        ))}
      </div>
      {data && data.skills.length === 0 && (
        <div className="at-panel at-fade-in" style={{ padding: 56, textAlign: "center", color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)", fontSize: 13 }}>
          ◌ EMPTY · skills/ 目录无 .md 技能文件
        </div>
      )}
    </div>
  );
}
