import { describe, expect, it } from "vitest";
import { parseUnifiedDiff } from "./diff";

describe("parseUnifiedDiff", () => {
  it("按行首标记分类 diff 行", () => {
    const diff = [
      "--- a/app.py",
      "+++ b/app.py",
      "@@ -1,3 +1,3 @@",
      " unchanged",
      "-old line",
      "+new line",
    ].join("\n");
    const lines = parseUnifiedDiff(diff);
    expect(lines.map((l) => l.type)).toEqual([
      "meta", "meta", "hunk", "context", "del", "add",
    ]);
    expect(lines[4].text).toBe("-old line");
    expect(lines[5].text).toBe("+new line");
  });

  it("空 diff 返回空数组", () => {
    expect(parseUnifiedDiff("")).toEqual([{ type: "context", text: "" }]);
  });

  it("上下文行不被误判(空行/普通文本)", () => {
    const lines = parseUnifiedDiff("\nplain text");
    expect(lines[0].type).toBe("context");
    expect(lines[1].type).toBe("context");
  });
});
