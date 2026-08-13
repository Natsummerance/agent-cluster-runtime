// 三轴仪表盘纯函数（设计 §10.1 唯一计算式；状态枚举 ok|warn|critical）
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

export interface SessionHealth {
  eval_pass_rate_trend?: { latest?: number; history?: number[] } | null;
  token_cost?: { used?: number; budget?: number; cost?: number; currency?: string } | null;
  estimate_accuracy?: number | null;
  rework_rate?: number | null;
  [key: string]: unknown;
}

export interface HealthAxis {
  score: number;
  status: AxisStatus;
  sessions: Record<string, SessionHealth>;
}

export interface ProgressSessionInput {
  status?: string;
  phases?: Array<{ status?: string }> | Record<string, { status?: string }> | null;
}

export interface HealthSessionInput {
  session_id: string;
  over_budget?: boolean;
  health?: SessionHealth | null;
}

function round4(value: number): number {
  return Math.round(value * 10000) / 10000;
}

function clamp01(value: number): number {
  return Math.min(1, Math.max(0, value));
}

export function costAxis(
  used: number,
  limit: number,
  warnRatio: number,
  estimatedUsd = 0,
): CostAxis {
  const ratio = limit > 0 ? used / limit : 0;
  const score = Math.max(0, 1 - ratio);
  let status: AxisStatus = 'ok';
  if (limit > 0 && ratio >= 1) status = 'critical';
  else if (limit > 0 && ratio >= warnRatio) status = 'warn';
  return {
    used,
    limit,
    ratio: round4(ratio),
    score: round4(score),
    status,
    estimated_usd: round4(estimatedUsd),
  };
}

function sessionPhaseRatio(session: ProgressSessionInput): number {
  const phases = session.phases;
  const list = Array.isArray(phases)
    ? phases
    : phases && typeof phases === 'object'
      ? Object.values(phases)
      : [];
  if (list.length === 0) {
    return session.status === 'completed' ? 1 : 0;
  }
  const done = list.filter((phase) => phase?.status === 'done').length;
  return done / list.length;
}

export function progressAxis(sessions: ProgressSessionInput[]): ProgressAxis {
  let total = 0;
  let done = 0;
  const values: number[] = [];
  for (const session of sessions) {
    const phases = session.phases;
    const list = Array.isArray(phases)
      ? phases
      : phases && typeof phases === 'object'
        ? Object.values(phases)
        : [];
    total += list.length;
    done += list.filter((phase) => phase?.status === 'done').length;
    values.push(sessionPhaseRatio(session));
  }
  const score = values.length ? round4(values.reduce((a, b) => a + b, 0) / values.length) : 0;
  let status: AxisStatus;
  if (values.length === 0) status = 'ok';
  else if (score >= 0.7) status = 'ok';
  else if (score >= 0.4) status = 'warn';
  else status = 'critical';
  return { score, status, phases: { total, done } };
}

function sessionHealthScore(session: HealthSessionInput): number | null {
  const health = session.health;
  if (!health) return null;
  const subs: number[] = [];
  const trend = health.eval_pass_rate_trend;
  if (trend && typeof trend.latest === 'number') subs.push(clamp01(trend.latest));
  const tokenCost = health.token_cost;
  if (tokenCost && (tokenCost.budget ?? 0) > 0) {
    subs.push(clamp01(1 - (tokenCost.used ?? 0) / (tokenCost.budget ?? 1)));
  }
  if (typeof health.estimate_accuracy === 'number') subs.push(clamp01(health.estimate_accuracy));
  if (typeof health.rework_rate === 'number') subs.push(clamp01(1 - health.rework_rate));
  if (subs.length === 0) return null;
  return subs.reduce((a, b) => a + b, 0) / subs.length;
}

export function healthAxis(sessions: HealthSessionInput[]): HealthAxis {
  const sessionMap: Record<string, SessionHealth> = {};
  const values: number[] = [];
  let anyCritical = false;
  for (const session of sessions) {
    const health = session.health ?? {};
    sessionMap[session.session_id] = health;
    if (session.over_budget) anyCritical = true;
    if (typeof health.rework_rate === 'number' && health.rework_rate >= 0.5) anyCritical = true;
    const score = sessionHealthScore(session);
    if (score !== null) values.push(score);
  }
  const score = values.length ? round4(values.reduce((a, b) => a + b, 0) / values.length) : 0;
  let status: AxisStatus;
  if (anyCritical || (values.length > 0 && score < 0.4)) status = 'critical';
  else if (values.length > 0 && score < 0.7) status = 'warn';
  else status = 'ok';
  return { score, status, sessions: sessionMap };
}
