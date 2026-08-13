// 各端点函数（路径与方法对齐 agent-cluster serve v0.5 契约）
import { apiRequest, subscribeSse } from './client';
import type {
  AuditData,
  ChangeData,
  CreateProjectInput,
  CreateSessionInput,
  CreateSessionResult,
  EvolutionProposal,
  IntegrationNote,
  MemoryData,
  MetricsData,
  Project,
  SessionEvent,
  SessionSnapshot,
  StatusData,
  WorkspaceFile,
  WorkspaceTree,
} from './types';

export * from './types';

function sidPath(sid: string, suffix = ''): string {
  return `/api/v1/sessions/${encodeURIComponent(sid)}${suffix}`;
}

function pidPath(pid: string, suffix = ''): string {
  return `/api/v1/projects/${encodeURIComponent(pid)}${suffix}`;
}

// ---- 状态与总览 ----
export const fetchStatus = () => apiRequest<StatusData>('/api/v1/status');
export const fetchMetrics = () => apiRequest<MetricsData>('/api/v1/metrics');

// ---- 项目 ----
export const fetchProjects = () => apiRequest<Project[]>('/api/v1/projects');
export const createProject = (input: CreateProjectInput) =>
  apiRequest<Project>('/api/v1/projects', { method: 'POST', body: input });

// ---- 会话 ----
export const fetchProjectSessions = (projectId: string) =>
  apiRequest<SessionSnapshot[]>(pidPath(projectId, '/sessions'));
export const createSession = (projectId: string, input: CreateSessionInput) =>
  apiRequest<CreateSessionResult>(pidPath(projectId, '/sessions'), {
    method: 'POST',
    body: input,
  });
export const fetchSession = (sid: string) =>
  apiRequest<SessionSnapshot>(sidPath(sid));

export function subscribeSessionEvents(
  sid: string,
  onEvent: (event: SessionEvent) => void,
  options?: { since?: number; signal?: AbortSignal; onError?: (err: unknown) => void },
): () => void {
  return subscribeSse<SessionEvent>(
    sidPath(sid, '/events'),
    onEvent,
    options,
  );
}

export const fetchSessionChanges = (sid: string) =>
  apiRequest<ChangeData>(sidPath(sid, '/changes'));

export const approveSession = (sid: string) =>
  apiRequest<Record<string, unknown>>(sidPath(sid, '/approve'), { method: 'POST' });
export const rejectSession = (sid: string) =>
  apiRequest<Record<string, unknown>>(sidPath(sid, '/reject'), { method: 'POST' });
export const editSession = (sid: string, text: string) =>
  apiRequest<Record<string, unknown>>(sidPath(sid, '/edit'), { method: 'POST', body: { text } });
export const respondSession = (sid: string, text: string) =>
  apiRequest<Record<string, unknown>>(sidPath(sid, '/response'), { method: 'POST', body: { text } });
export const interruptSession = (sid: string, text: string) =>
  apiRequest<Record<string, unknown>>(sidPath(sid, '/interrupt'), { method: 'POST', body: { text } });
export const cancelSession = (sid: string) =>
  apiRequest<Record<string, unknown>>(sidPath(sid, '/cancel'), { method: 'POST' });
export const sendSessionStdin = (sid: string, text: string) =>
  apiRequest<Record<string, unknown>>(sidPath(sid, '/stdin'), { method: 'POST', body: { text } });
export const rollbackSession = (sid: string, version: string | number) =>
  apiRequest<Record<string, unknown>>(sidPath(sid, '/rollback'), { method: 'POST', body: { version } });

// ---- 审计 ----
export const fetchSessionAudit = (sid: string) =>
  apiRequest<AuditData>(sidPath(sid, '/audit'));
export const exportSessionAudit = (sid: string) =>
  apiRequest<AuditData>(sidPath(sid, '/audit/export'), { method: 'POST' });

// ---- 工作区 ----
export const fetchWorkspaceTree = (projectId: string, path = '') =>
  apiRequest<WorkspaceTree>(pidPath(projectId, '/workspace/tree'), {
    query: { path: path || undefined },
  });
export const fetchWorkspaceFile = (projectId: string, path: string) =>
  apiRequest<WorkspaceFile>(pidPath(projectId, '/workspace/file'), {
    query: { path },
  });

// ---- 记忆 ----
export const fetchMemory = (projectId: string) =>
  apiRequest<MemoryData>(pidPath(projectId, '/memory'));
export const promoteMemory = (memoryId: string) =>
  apiRequest<Record<string, unknown>>(`/api/v1/memory/${encodeURIComponent(memoryId)}/promote`, {
    method: 'POST',
  });

// ---- 集成（可能返回 {note} 占位，前端两者兼容） ----
export const fetchPlugins = () => apiRequest<unknown[] | IntegrationNote>('/api/v1/plugins');
export const fetchSkills = () => apiRequest<unknown[] | IntegrationNote>('/api/v1/skills');
export const fetchMcp = () => apiRequest<unknown[] | IntegrationNote>('/api/v1/mcp');

// ---- 进化 ----
export const fetchEvolutionProposals = (projectId?: string) =>
  apiRequest<EvolutionProposal[]>(`/api/v1/evolution/proposals`, {
    query: { project_id: projectId || undefined },
  });
export const generateEvolutionProposals = (input: {
  project_id?: string;
  min_evidence?: number;
  limit?: number;
}) => apiRequest<unknown>('/api/v1/evolution/generate', { method: 'POST', body: input });
export const applyEvolutionProposal = (proposalId: string) =>
  apiRequest<unknown>(`/api/v1/evolution/proposals/${encodeURIComponent(proposalId)}/apply`, {
    method: 'POST',
  });
export const rollbackEvolutionProposal = (proposalId: string) =>
  apiRequest<unknown>(`/api/v1/evolution/proposals/${encodeURIComponent(proposalId)}/rollback`, {
    method: 'POST',
  });
export const runEvolutionRetro = (input: { project_id?: string; session_id?: string }) =>
  apiRequest<unknown>('/api/v1/evolution/retro', { method: 'POST', body: input });