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
  auth?: { enabled: boolean; user?: string | null };
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

export interface AuditTrajectoryEvent {
  seq?: number;
  ts?: string;
  type?: string;
  actor?: string;
  payload?: unknown;
}

export interface AuditData {
  session_id?: string;
  goal?: string;
  records?: unknown[];
  summary?: string;
  file?: string;
  content?: string;
  events?: AuditTrajectoryEvent[];
  [key: string]: unknown;
}

export interface AuditExportData {
  session_id?: string;
  format?: string;
  retention_days?: number | null;
  content?: string;
  files?: Record<string, string>;
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

export interface LoginResult {
  user: string;
  access_token: string;
  refresh_token: string;
}

// ---- 多租户（v0.7 T14.12）----
export interface Tenant {
  id: string;
  name: string;
  project_limit: number;
  session_limit: number;
  created_at?: string;
}

// ---- 资源日历（v0.7 T14.15）----
export interface Availability {
  id: string;
  role_id: string;
  start: string;
  end: string;
  note?: string;
  created_at?: string;
}

export interface CalendarData {
  availability: Availability[];
}

export interface TenantUsage {
  projects: number;
  sessions: number;
  project_limit: number;
  session_limit: number;
}

// ---- 跨项目依赖图（v0.7 T14.16）----
export interface DependencyEdge {
  id: string;
  from_project: string;
  to_project: string;
  from_task?: string;
  to_task?: string;
  type?: string;
  created_at?: string;
}

export interface DependenciesData {
  edges: DependencyEdge[];
}

export interface DependencyImpactData {
  project_id: string;
  impact: string[];
}

// ---- 高级编排（v0.7 T14.17：plan/goal/jobs/schedule）----
export interface Plan {
  id: string;
  name?: string;
  mode?: string;
  goals?: string[];
  jobs?: string[];
  created_at?: string;
  updated_at?: string;
}

export interface Goal {
  id: string;
  plan_id?: string;
  objective: string;
  status?: string;
  rounds?: number;
  version?: number;
  max_rounds?: number;
  blocked_reason?: Record<string, string> | null;
  created_at?: string;
  updated_at?: string;
}

export interface Job {
  id: string;
  owner: string;
  state?: string;
  outcome?: string;
  settled_at?: string | null;
  plan_id?: string | null;
  created_at?: string;
}

export interface Schedule {
  id: string;
  kind: string;
  at?: string | null;
  after_minutes?: number | null;
  every_minutes?: number | null;
  state?: string;
  created_at?: string;
}

export interface PlansData {
  plans: Plan[];
}

export interface PlanDetailData {
  plan: Plan;
  goals: Goal[];
  jobs: Job[];
}

export interface SchedulesData {
  schedules: Schedule[];
}
