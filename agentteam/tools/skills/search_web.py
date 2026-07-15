"""search_web stub 技能：模拟网络搜索,返回基于查询的 mock 结果。

不调用真实搜索 API,仅用于演示 Worker ReAct 工具循环。
"""
from __future__ import annotations

from langchain_core.tools import tool


@tool
def search_web(query: str, max_results: int = 3) -> str:
    """搜索网络,返回相关结果摘要。

    Args:
        query: 搜索关键词。
        max_results: 最大结果条目数,默认 3。

    Returns:
        模拟搜索结果文本,每条包含序号、标题、URL 和摘要。
    """
    lines = [f"搜索「{query}」的结果："]
    for i in range(1, max_results + 1):
        lines.append(f"[结果 {i}] {query} - 相关资料 {i}")
        lines.append(f"  URL: https://example.com/search?q={query.replace(' ', '+')}&p={i}")
        lines.append(f"  摘要: 关于「{query}」的第 {i} 条参考信息。")
    return "\n".join(lines)
