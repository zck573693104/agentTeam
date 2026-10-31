/** unified diff 文本 → 带类型的行列表(审批 diff 预览着色用)。 */

export type DiffLineType = "add" | "del" | "hunk" | "meta" | "context";

export interface DiffLine {
  type: DiffLineType;
  text: string;
}

/** 按行首标记分类: @@/--- +++(文件头)优先于 +/-,其余 +/-/上下文。 */
export function parseUnifiedDiff(diff: string): DiffLine[] {
  return diff.split("\n").map((line) => {
    if (line.startsWith("---") || line.startsWith("+++")) {
      return { type: "meta", text: line };
    }
    if (line.startsWith("+")) return { type: "add", text: line };
    if (line.startsWith("-")) return { type: "del", text: line };
    if (line.startsWith("@@")) return { type: "hunk", text: line };
    return { type: "context", text: line };
  });
}
