/**
 * MiniMarkdown — 轻量报告渲染(无第三方依赖)。
 * 支持: #/##/### 标题、- 列表、--- 分隔线、**加粗**、关键词语义着色
 * (阻塞/失败/迟到/等待人工=红,正常/无异常=绿),供晨报/复盘类工单 result 展示。
 */
import type { ReactElement } from "react";

const RED = /(阻塞|失败|迟到|等待人工|无数据)/;
const GREEN = /(正常|无异常|全部挂载)/;
const KEYWORD = /(阻塞|失败|迟到|等待人工|无数据|正常|无异常|全部挂载)/g;

function Inline({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((p, i) => {
        if (p.startsWith("**") && p.endsWith("**") && p.length > 4) {
          return <b key={i} style={{ color: "var(--at-gold)" }}>{p.slice(2, -2)}</b>;
        }
        if (!RED.test(p) && !GREEN.test(p)) return <span key={i}>{p}</span>;
        const segs = p.split(KEYWORD);
        return (
          <span key={i}>
            {segs.map((s, j) => {
              if (RED.test(s) && s.length <= 6) {
                return <span key={j} style={{ color: "var(--at-red)" }}>{s}</span>;
              }
              if (GREEN.test(s) && s.length <= 6) {
                return <span key={j} style={{ color: "var(--at-green)" }}>{s}</span>;
              }
              return <span key={j}>{s}</span>;
            })}
          </span>
        );
      })}
    </>
  );
}

function h2Color(title: string): string {
  if (title.includes("风险")) return "var(--at-amber)";
  if (title.includes("总览")) return "var(--at-blue)";
  return "var(--at-text-dim)";
}

export default function MiniMarkdown({ source }: { source: string }) {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactElement[] = [];
  let listBuf: { text: string; depth: number }[] = [];

  const flushList = () => {
    if (!listBuf.length) return;
    blocks.push(
      <div key={`ul-${blocks.length}`} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {listBuf.map((it, i) => (
          <div key={i} style={{
            display: "flex", gap: 8, fontSize: 13, lineHeight: 1.65,
            color: "var(--at-text)", marginLeft: it.depth * 16,
          }}>
            <span style={{ color: "var(--at-cyan)", flexShrink: 0, fontFamily: "var(--at-font-mono)" }}>
              {it.depth === 0 ? "▸" : "·"}
            </span>
            <span><Inline text={it.text} /></span>
          </div>
        ))}
      </div>,
    );
    listBuf = [];
  };

  lines.forEach((raw, idx) => {
    const line = raw.trimEnd();
    if (!line.trim()) { flushList(); return; }
    if (/^- /.test(line.trim())) {
      listBuf.push({ text: line.trim().slice(2), depth: (raw.length - raw.trimStart().length) > 1 ? 1 : 0 });
      return;
    }
    flushList();
    if (line.startsWith("### ")) {
      blocks.push(
        <div key={idx} style={{ marginTop: 14, marginBottom: 4 }}>
          <span className="at-tag" style={{ color: "var(--at-green)", fontSize: 12 }}>{line.slice(4)}</span>
        </div>,
      );
      return;
    }
    if (line.startsWith("## ")) {
      const title = line.slice(3);
      blocks.push(
        <div key={idx} style={{
          marginTop: 16, marginBottom: 8, paddingBottom: 4,
          borderBottom: "1px solid var(--at-border-soft)",
          fontFamily: "var(--at-font-mono)", fontSize: 13, letterSpacing: "0.08em",
          color: h2Color(title), display: "flex", alignItems: "center", gap: 8,
        }}>
          <span style={{ color: "var(--at-amber)" }}>§</span>{title}
        </div>,
      );
      return;
    }
    if (line.startsWith("# ")) {
      blocks.push(
        <div key={idx} style={{
          display: "flex", alignItems: "baseline", gap: 10,
          padding: "2px 0 10px", borderLeft: "3px solid var(--at-amber)",
          paddingLeft: 10, marginBottom: 4,
        }}>
          <b style={{ fontSize: 17 }}>{line.slice(2).replace(/ · (\d{4}-\d{2}-\d{2})$/, "")}</b>
          {line.match(/(\d{4}-\d{2}-\d{2})$/) && (
            <span style={{ fontSize: 12, color: "var(--at-text-faint)", fontFamily: "var(--at-font-mono)" }}>
              {line.match(/(\d{4}-\d{2}-\d{2})$/)![1]}
            </span>
          )}
        </div>,
      );
      return;
    }
    if (/^-{3,}$/.test(line.trim())) {
      blocks.push(<div key={idx} style={{ borderTop: "1px solid var(--at-border)", margin: "12px 0" }} />);
      return;
    }
    blocks.push(
      <div key={idx} style={{ fontSize: 13, lineHeight: 1.65, color: "var(--at-text-dim)", marginTop: 4 }}>
        <Inline text={line} />
      </div>,
    );
  });
  flushList();

  return <div style={{ display: "flex", flexDirection: "column" }}>{blocks}</div>;
}
