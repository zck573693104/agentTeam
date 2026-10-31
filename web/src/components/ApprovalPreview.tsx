import type { ApprovalPreviewData } from "../api/client";
import { parseUnifiedDiff } from "../utils/diff";
const DIFF_LINE_COLOR: Record<string, string> = {
  add: "var(--at-green)",
  del: "var(--at-red)",
  hunk: "var(--at-blue)",
  meta: "var(--at-text-faint)",
  context: "var(--at-text-dim)",
};

const MONO: React.CSSProperties = {
  fontFamily: "var(--at-font-mono)",
  fontSize: 11,
  lineHeight: 1.6,
  margin: 0,
  whiteSpace: "pre-wrap",
  wordBreak: "break-all",
};

function PreviewHeader({ preview }: { preview: ApprovalPreviewData }) {
  const label =
    preview.kind === "diff" ? "FILE DIFF · 将写入以下变更"
    : preview.kind === "content" ? "FILE CONTENT · 将写入(新文件或读不到现文件)"
    : preview.kind === "command" ? "COMMAND · 将执行"
    : preview.kind === "path" ? "PATH · 访问目标"
    : "ARGS · 工具入参";
  return (
    <div style={{
      display: "flex",
      alignItems: "center",
      gap: 8,
      marginBottom: 8,
      fontFamily: "var(--at-font-mono)",
      fontSize: 10,
      letterSpacing: "0.12em",
      color: "var(--at-text-faint)",
    }}>
      <span style={{ color: "var(--at-amber)" }}>▸ {preview.tool}</span>
      <span>{label}</span>
      {preview.truncated && (
        <span style={{ color: "var(--at-amber)" }}>· 内容已截断</span>
      )}
    </div>
  );
}

/** 待审批工具调用的预览块(diff / content / command / path / args)。 */
export default function ApprovalPreview({
  preview,
  gate,
}: {
  preview: ApprovalPreviewData;
  gate?: Record<string, unknown> | null;
}) {
  return (
    <div className="at-fade-in" style={{
      borderTop: "1px solid var(--at-amber)",
      marginTop: 10,
      paddingTop: 10,
    }}>
      <PreviewHeader preview={preview} />
      {preview.kind === "diff" && preview.diff && (
        <pre style={{
          ...MONO,
          padding: 10,
          background: "var(--at-bg-deep)",
          border: "1px solid var(--at-border)",
          maxHeight: 320,
          overflow: "auto",
        }}>
          {parseUnifiedDiff(preview.diff).map((line, i) => (
            <div key={i} style={{ color: DIFF_LINE_COLOR[line.type] }}>
              {line.text || " "}
            </div>
          ))}
        </pre>
      )}
      {preview.kind === "content" && preview.content !== undefined && (
        <pre style={{
          ...MONO,
          padding: 10,
          background: "var(--at-bg-deep)",
          border: "1px solid var(--at-border)",
          color: "var(--at-text)",
          maxHeight: 320,
          overflow: "auto",
        }}>
          {preview.content}
          {preview.total_lines !== undefined && (
            <span style={{ color: "var(--at-text-faint)" }}>
              {"\n"}— 共 {preview.total_lines} 行 —
            </span>
          )}
        </pre>
      )}
      {preview.kind === "command" && (
        <pre style={{
          ...MONO,
          padding: 10,
          background: "var(--at-bg-deep)",
          border: "1px solid var(--at-border)",
          color: "var(--at-text)",
        }}>
          <span style={{ color: "var(--at-green)" }}>$ </span>
          {preview.command}
        </pre>
      )}
      {preview.kind === "path" && (
        <div style={{
          ...MONO,
          padding: "8px 10px",
          background: "var(--at-bg-deep)",
          border: "1px solid var(--at-border)",
          color: "var(--at-text)",
        }}>
          {preview.path}
        </div>
      )}
      {preview.kind === "args" && (
        <pre style={{
          ...MONO,
          padding: 10,
          background: "var(--at-bg-deep)",
          border: "1px solid var(--at-border)",
          color: "var(--at-text-dim)",
          maxHeight: 200,
          overflow: "auto",
        }}>
          {/* 无结构化预览时展示 gate 的入参(截断版)或摘要文本 */}
          {gate && typeof gate.input === "object" && gate.input !== null
            ? JSON.stringify(gate.input, null, 2)
            : typeof gate?.args === "string"
              ? gate.args
              : JSON.stringify(preview, null, 2)}
        </pre>
      )}
    </div>
  );
}
