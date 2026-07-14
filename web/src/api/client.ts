/** 轻量 fetch 封装 + 后端响应类型定义。 */

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

// ---- 类型定义(与后端 Pydantic 模型 / SQLite schema 对齐) ----

export interface Run {
  run_id: string;
  team_name: string;
  task: string;
  status: "pending" | "running" | "completed" | "failed" | "interrupted";
  created_at: string;
  updated_at: string;
  ended_at: string | null;
  total_tokens: number;
}

export interface CreateRunRequest {
  team_name: string;
  task: string;
}

export interface ApproveRequest {
  approved: boolean;
  reason?: string | null;
}

export interface Dashboard {
  total_runs: number;
  total_tokens: number;
  by_status: Record<string, number>;
  by_team: Record<string, number>;
  recent_runs: Run[];
}

export interface TraceEvent {
  id: number;
  run_id: string;
  event_type: string;
  actor: string;
  timestamp: string;
  payload: string;
  duration_ms: number | null;
  tokens: number | null;
}

export interface Approval {
  id: string;
  run_id: string;
  status: string;
  requested_at: string;
  decided_at: string | null;
  decider: string | null;
  reason: string | null;
}

export interface Team {
  name: string;
  description: string;
  leader: Record<string, any>;
  workers: Record<string, any>[];
  default_model: Record<string, any> | null;
  skills: string[];
  mcp_servers: Record<string, any>[];
}
