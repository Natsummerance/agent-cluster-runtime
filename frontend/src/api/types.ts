// 后端契约类型（agent-cluster serve v0.5）

export interface DoctorCheck {
  name: string;
  ok: boolean;
  required: boolean;
  detail: string;
  action: string;
}

export interface DoctorReport {
  ok: boolean;
  checks: DoctorCheck[];
  fix?: { ran: boolean; exit_code: number; output: string } | null;
}

export interface ApiEnvelope<T> {
  ok: boolean;
  data?: T;
  error?: string;
}

export interface StatusData {
  version: string;
  projects: number;
  sessions: number;
  active_sessions: number;
  uptime: number;
}

export interface Project {
  id: string;
  name: string;
  workspace: string;
  status: string;
  created_at: string;
}

export type SessionStatus = 'running' | 'waiting_approval' | 'completed' | 'failed';

export interface TokenInfo {
  budget: number;
  used: number;
  remaining: number;
  over_budget: boolean;
  by_phase?: Record<string, number>;
  by_role?: Record<string, number>;
}

export interface HealthInfo {
  eval_pass_rate_trend?: number[];
  token_cost?: number;
  estimate_accuracy?: number;
  rework_rate?: number;
}

export type AxisStatus = 'ok' | 'warn' | 'critical';

export interface CostAxis {
  used: number;
  limit: number;
  ratio: number;
  score: number;
  status: AxisStatus;
  estimated_usd: number;
}

export interface ProgressAxis {
  score: number;
  status: AxisStatus;
  phases: { total: number; done: number };
}

export interface SessionHealthInfo {
  eval_pass_rate_trend?: { latest?: number; history?: number[] } | null;
  token_cost?: { used?: number; budget?: number; cost?: number; currency?: string } | null;
  estimate_accuracy?: number | null;
  rework_rate?: number | null;
  [key: string]: unknown;
}

export interface HealthAxis {
  score: number;
  status: AxisStatus;
  sessions: Record<string, SessionHealthInfo>;
}

export interface DashboardData {
  cost: CostAxis;
  progress: ProgressAxis;
  health: HealthAxis;
  updated_at: string;
}

export interface TaskEntry {
  session_id: string;
  goal?: string;
  status?: string;
  runtime_status?: string;
  assignee?: string;
  workspace?: string;
  worktree?: boolean;
  model?: string;
  current_phase?: string | null;
  current_node?: string | null;
  created_at?: string;
  updated_at?: string;
  metadata?: Record<string, string>;
  [key: string]: unknown;
}

export interface ForkResult {
  session_id: string;
  parent_session_id: string;
  fork_depth: number;
}

export interface StdinResponse {
  accepted: string;
}

export interface SessionSnapshot {
  session_id: string;
  project_id: string;
  workspace: string;
  goal: string;
  model?: string;
  status: SessionStatus | string;
  pending_hint?: string | null;
  current_phase?: string | null;
  current_node?: string | null;
  token: TokenInfo;
  phases?: string[];
  transcript_count?: number;
  gate_count?: number;
  health?: HealthInfo | null;
  error?: string | null;
  exit_code?: number | null;
}

export interface SessionEvent {
  seq?: number;
  type: string;
  ts?: string;
  data?: Record<string, unknown> | string | number | boolean | null;
  [key: string]: unknown;
}

export interface ChangeRecord {
  version?: string | number;
  ts?: string;
  summary?: string;
  type?: string;
  [key: string]: unknown;
}

export interface ChangeData {
  records: ChangeRecord[];
  summary?: string;
}

export interface WorkspaceTreeEntry {
  name: string;
  type: 'dir' | 'file';
  size?: number;
}

export interface WorkspaceTree {
  path: string;
  entries: WorkspaceTreeEntry[];
}

export interface WorkspaceFileInfo {
  name: string;
  size: number;
  content: string;
  mime: string;
}

export interface WorkspaceFile {
  path: string;
  file: WorkspaceFileInfo;
}

export interface MemoryItem {
  id: string;
  content: string;
  tags?: string[];
  created_at?: string;
  source?: string;
  [key: string]: unknown;
}

export interface MemoryData {
  items: MemoryItem[];
  proposals: MemoryItem[];
}

export interface MetricsData {
  sessions: number;
  active: number;
  total_tokens: number;
  total_cost: number;
  health: Record<string, unknown> | null;
  updated_at: string;
}

export interface EvolutionProposal {
  id: string;
  title?: string;
  summary?: string;
  status?: string;
  evidence?: string;
  created_at?: string;
  [key: string]: unknown;
}

export interface CreateProjectInput {
  name: string;
  workspace: string;
}

export interface CreateSessionInput {
  goal: string;
  model?: string;
  flow?: string;
  budget?: number;
  deterministic?: boolean;
  yes?: boolean;
}

export interface CreateSessionResult {
  session_id: string;
  project_id: string;
  workspace: string;
}

export interface AuditData {
  session_id?: string;
  records?: unknown[];
  summary?: string;
  file?: string;
  content?: string;
  [key: string]: unknown;
}

export interface IntegrationNote {
  note?: string;
  [key: string]: unknown;
}
// ---- RBAC（v0.7 T14.9）----
export interface RbacRole {
  id: string;
  name: string;
  kind: string;
  permissions: string[];
}

export interface RbacUser {
  id: string;
  name: string;
  role_ids: string[];
  scopes: string[];
  is_admin?: boolean;
}

export interface RbacTeam {
  id: string;
  name: string;
  member_ids: string[];
}
