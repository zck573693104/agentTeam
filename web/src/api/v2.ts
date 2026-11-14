/** v2 控制台 API client(dsh 式工单中心;类型与后端 agentteam/v2 响应对齐)。 */

import { api } from "./client";

// ---- 类型 ----
export interface V2Worker {
  name: string;
  system_prompt: string;
  model: Record<string, any> | null;
  tools: string[];
  skills: string[];
  approval_targets: string[];
  max_iterations: number;
  enabled: boolean;
}

export interface V2Mandate {
  basis: "explicit" | "inferred";
  submitted_by: string;
  policy_version: string;
  scope: string | null;
}

export interface V2Ticket {
  id: string;
  title: string;
  task: string;
  status: "pending" | "running" | "waiting_human" | "done" | "failed" | "cancelled";
  worker_name: string | null;
  thread_id: string | null;
  mandate: V2Mandate | null;
  result: string | null;
  created_at: string;
  updated_at: string;
  ended_at: string | null;
  sessions?: V2Session[];
}

export interface V2Session {
  id: string;
  ticket_id: string;
  worker_name: string;
  workspace_dir: string;
  status: string;
  iterations: number;
  max_iterations: number;
  created_at: string;
  updated_at: string;
}

export interface V2Message {
  id: number;
  session_id: string;
  role: "system" | "user" | "assistant" | "tool";
  content: string;
  tool_calls: { name: string; args: Record<string, any>; id: string }[] | null;
  tool_call_id: string | null;
  created_at: string;
}

export interface V2Event {
  id: number;
  ticket_id: string;
  session_id: string | null;
  type: string;
  actor: string;
  payload: Record<string, any>;
  created_at: string;
}

export interface V2Attention {
  id: string;
  ticket_id: string;
  session_id: string;
  type: "approval" | "question";
  payload: {
    tool?: string;
    args?: Record<string, any>;
    worker?: string;
    question?: string;
    draft_reply?: string;
    decision_effect?: string;
  };
  status: "pending" | "decided";
  decision: string | null;
  decided_by: string | null;
  reason: string | null;
  created_at: string;
  decided_at: string | null;
}

export interface V2Thread {
  id: string;
  source: string;
  thread_key: string;
  title: string | null;
  created_at: string;
  triggers?: V2Trigger[];
  tickets?: V2Ticket[];
}

export interface V2Trigger {
  id: string;
  thread_id: string;
  actor: string;
  text: string;
  at: string;
  raw?: Record<string, any>;
}

export interface V2Source {
  name: string;
  kind: "webhook" | "github" | "feishu";
  config: Record<string, any>;
  enabled: boolean;
  created_at: string;
}

export interface V2Evaluation {
  id: string;
  trigger_id: string;
  gate: Record<string, any>;
  decision: "accepted" | "rejected" | "would_accept";
  reason: string | null;
  ticket_id: string | null;
  created_at: string;
}

// ---- 通用 ----
const BASE = "/api/v2";
export const v2Get = <T>(path: string) => api<T>(BASE + path);
export const v2Post = <T>(path: string, body: unknown) =>
  api<T>(BASE + path, { method: "POST", body: JSON.stringify(body) });
export const v2Put = <T>(path: string, body: unknown) =>
  api<T>(BASE + path, { method: "PUT", body: JSON.stringify(body) });
export const v2Delete = <T>(path: string) => api<T>(BASE + path, { method: "DELETE" });
