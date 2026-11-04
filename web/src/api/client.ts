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
  status:
    | "pending"
    | "running"
    | "completed"
    | "failed"
    | "interrupted"
    | "cancelling"
    | "cancelled";
  created_at: string;
  updated_at: string;
  ended_at: string | null;
  total_tokens: number;
  // SP12 Mandate:授权凭据(旧 run 无凭据为 null)
  mandate: {
    basis: "explicit" | "inferred";
    submitted_by: string;
    policy_version: string;
    scope: string | null;
  } | null;
}

export interface CreateRunRequest {
  team_name: string;
  task: string;
}

// ---- SP12d 绿色通道(POST /api/runs/{id}/messages) ----

export interface FollowUpResponse {
  ok: boolean;
  target: "worker" | "leader";
  queue_depth: number;
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
  // 审批 diff 预览: tool 级审批决策落账时由 gate 带入(step/worker 级为 null)
  tool_name?: string | null;
  args_preview?: string | null;
}

// ---- 审批 diff 预览(GET /api/runs/{id}/pending-approval) ----

/** write_file 类审批的预览:能读到现文件时给 unified diff,否则给新内容全文 */
export interface WritePreview {
  kind: "diff" | "content";
  tool: string;
  path: string;
  diff?: string;
  content?: string;
  total_lines?: number;
  truncated: boolean;
}

export interface CommandPreview {
  kind: "command";
  tool: string;
  command: string;
  truncated: boolean;
}

export interface PathPreview {
  kind: "path";
  tool: string;
  path: string;
  truncated: boolean;
}

export interface ArgsPreview {
  kind: "args";
  tool: string;
  truncated: boolean;
}

export type ApprovalPreviewData =
  | WritePreview
  | CommandPreview
  | PathPreview
  | ArgsPreview;

export interface PendingApproval {
  pending: boolean;
  /** park 快照里的审批门(tool 级含 input;无快照如 langgraph interrupt 为 null) */
  gate: Record<string, unknown> | null;
  preview: ApprovalPreviewData | null;
}

// ---- 记忆层(GET /api/teams/{name}/memories) ----

export interface TeamMemory {
  id: number;
  team_name: string;
  run_id: string;
  outcome: string;
  summary: string;
  meta: { key_points?: string[]; files?: string[] } | null;
  tokens: number;
  created_at: string;
}

export interface AgentNode {
  name: string;
  role: "supervisor" | "worker";
  system_prompt?: string;
  model?: Record<string, any> | null;
  children?: (AgentNode | TeamRefNode)[];
  approval_policy?: Record<string, any> | null;
  tools?: string[];
  max_iterations?: number;
  ref?: string | null;
  skills?: string[];
  version?: number;
}

export interface TeamRefNode {
  _type: "TeamRef";
  name: string;
  alias?: string | null;
}

export interface Team {
  name: string;
  description: string;
  root: AgentNode;
  default_model: Record<string, any> | null;
  skills: string[];
  mcp_servers: Record<string, any>[];
  engine?: "langgraph" | "opencode" | null;
}

// ---- SP7: Skills + Evolution ----

export interface SkillItem {
  name: string;
}

export interface SkillDetail {
  name: string;
  content: string;
}

export interface EvolutionRecord {
  id: number;
  agent_name: string;
  version: number;
  dimension: string;
  before_value: string;
  after_value: string;
  diff: string;
  reason: string;
  run_id: string | null;
  success: boolean;
  error: string | null;
  timestamp: string;
}

export interface VersionSnapshot {
  version: number;
  records: EvolutionRecord[];
}

export interface RollbackResult {
  ok: boolean;
  new_version: number;
}
