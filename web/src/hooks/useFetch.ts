import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

/** 默认请求超时(毫秒)。LLM 长查询走 SSE 不走 useFetch,30s 足够 REST 请求。 */
const DEFAULT_TIMEOUT_MS = 30_000;

/** 通用数据获取 hook:封装 loading / error / data + 手动 refetch + 请求超时。 */
export function useFetch<T>(path: string | null, timeoutMs: number = DEFAULT_TIMEOUT_MS) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(!!path);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const refetch = useCallback(() => setRefreshKey((k) => k + 1), []);

  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    // FE-E1:增加请求超时,避免 LLM 慢响应或网络挂起导致 loading 永远为 true。
    // 超时后 abort,catch 中识别 AbortError 但提示"请求超时"而非默认错误信息。
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    setLoading(true);
    api<T>(path, { signal: controller.signal })
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e: unknown) => {
        if (e instanceof DOMException && e.name === "AbortError") return;
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => setLoading(false));
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [path, refreshKey, timeoutMs]);

  return { data, loading, error, refetch };
}
