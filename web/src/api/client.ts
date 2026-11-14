/** 轻量 fetch 封装(具体类型见 v2.ts,与后端 agentteam/v2 响应对齐)。 */

const BASE = ""; // 同源:开发走 Vite 代理,生产走 FastAPI 静态

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(BASE + path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...((init?.headers as Record<string, string>) || {}),
    },
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    const detail =
      typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail);
    throw new Error(detail || `HTTP ${resp.status}`);
  }
  return resp.json();
}
