// e2e-real 助手：真实后端 REST 请求封装 + 轮询等待 + 确定性 mini-flow 生成。
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { APIRequestContext } from '@playwright/test';

export const BASE_URL = 'http://127.0.0.1:8765';
export const AUTH_TOKEN = 'ci';

export interface ApiResult {
  status: number;
  body: any;
}

export async function api(
  request: APIRequestContext,
  method: string,
  path: string,
  body?: unknown,
): Promise<ApiResult> {
  const res = await request.fetch(`${BASE_URL}${path}`, {
    method,
    headers: {
      'X-Auth-Token': AUTH_TOKEN,
      'Content-Type': 'application/json',
    },
    data: body === undefined ? undefined : JSON.stringify(body),
  });
  let parsed: any = null;
  try {
    parsed = await res.json();
  } catch {
    parsed = null;
  }
  return { status: res.status(), body: parsed };
}

export function expectOk(result: ApiResult, expectedStatus: number, label: string): any {
  if (result.status !== expectedStatus) {
    throw new Error(
      `${label}：期望 HTTP ${expectedStatus}，实际 ${result.status}（body=${JSON.stringify(result.body)}）`,
    );
  }
  if (!result.body || result.body.ok !== true) {
    throw new Error(`${label}：信封 ok !== true（body=${JSON.stringify(result.body)}）`);
  }
  return result.body.data;
}

export function uniqueSuffix(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

export function makeWorkspace(prefix = 'acr-e2e-real'): string {
  return mkdtempSync(join(tmpdir(), `${prefix}-`));
}

export interface MiniFlowOptions {
  threadId?: string;
  twoGates?: boolean;
  designOnly?: boolean;
}

export function miniFlowYaml(options: MiniFlowOptions = {}): string {
  const threadId = options.threadId ?? `t:${uniqueSuffix()}`;
  const lines = [`name: e2e-real-mini`, `thread_id: "${threadId}"`, 'nodes:'];
  const edges: string[] = [];
  if (options.designOnly) {
    lines.push('  - {id: start, type: start}');
    lines.push('  - {id: requirements, type: agent, role: pm}');
    lines.push('  - {id: gate_auto, type: gate, gate: design_review}');
    lines.push('  - {id: end, type: end}');
    edges.push(
      '  - {from: start, to: requirements}',
      '  - {from: requirements, to: gate_auto}',
      '  - {from: gate_auto, to: end, on_accept: end, on_reject: requirements}',
    );
  } else if (options.twoGates) {
    lines.push('  - {id: start, type: start}');
    lines.push('  - {id: requirements, type: agent, role: pm}');
    lines.push('  - {id: gate_req, type: gate, gate: requirement_confirmation}');
    lines.push('  - {id: gate_auto, type: gate, gate: design_review}');
    lines.push('  - {id: end, type: end}');
    edges.push(
      '  - {from: start, to: requirements}',
      '  - {from: requirements, to: gate_req}',
      '  - {from: gate_req, to: gate_auto, on_accept: gate_auto, on_reject: requirements}',
      '  - {from: gate_auto, to: end, on_accept: end, on_reject: requirements}',
    );
  } else {
    lines.push('  - {id: start, type: start}');
    lines.push('  - {id: requirements, type: agent, role: pm}');
    lines.push('  - {id: gate_req, type: gate, gate: requirement_confirmation}');
    lines.push('  - {id: end, type: end}');
    edges.push(
      '  - {from: start, to: requirements}',
      '  - {from: requirements, to: gate_req}',
      '  - {from: gate_req, to: end, on_accept: end, on_reject: requirements}',
    );
  }
  return [...lines, 'edges:', ...edges, ''].join('\n');
}

export interface ProjectFixture {
  projectId: string;
  workspace: string;
}

export async function createProject(
  request: APIRequestContext,
  name: string,
): Promise<ProjectFixture> {
  const workspace = makeWorkspace();
  const data = expectOk(
    await api(request, 'POST', '/api/v1/projects', { name, workspace }),
    201,
    '创建项目',
  );
  return { projectId: data.id, workspace };
}

export interface StartSessionOptions {
  goal?: string;
  yes?: boolean;
  twoGates?: boolean;
  designOnly?: boolean;
}

export async function startSession(
  request: APIRequestContext,
  fixture: ProjectFixture,
  options: StartSessionOptions = {},
): Promise<string> {
  const goal = options.goal ?? '待办应用';
  const flowName = `flow-${uniqueSuffix()}`;
  const flowPath = join(fixture.workspace, `${flowName}.yaml`);
  writeFileSync(
    flowPath,
    miniFlowYaml({ threadId: `t:${flowName}`, twoGates: options.twoGates, designOnly: options.designOnly }),
    'utf8',
  );
  const data = expectOk(
    await api(request, 'POST', `/api/v1/projects/${fixture.projectId}/sessions`, {
      goal,
      flow: flowPath,
      model: 'deterministic',
      deterministic: true,
      yes: options.yes ?? true,
    }),
    201,
    '启动会话',
  );
  return data.session_id;
}

export async function waitFor<T>(
  poll: () => Promise<T>,
  predicate: (value: T) => boolean,
  label: string,
  timeoutMs = 30_000,
  intervalMs = 100,
): Promise<T> {
  const deadline = Date.now() + timeoutMs;
  let last: T | undefined;
  while (Date.now() < deadline) {
    last = await poll();
    if (predicate(last)) {
      return last;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(`${label}：超时（${timeoutMs}ms），最后值=${JSON.stringify(last)}`);
}

export function getSnapshot(request: APIRequestContext, sid: string): Promise<any> {
  return api(request, 'GET', `/api/v1/sessions/${sid}`).then((result) => {
    expectOk(result, 200, '会话详情');
    return result.body.data;
  });
}

export function waitForStatus(
  request: APIRequestContext,
  sid: string,
  status: string,
): Promise<any> {
  return waitFor(
    () => getSnapshot(request, sid),
    (snap) => snap?.status === status,
    `会话 ${sid} 进入 ${status}`,
  );
}

export function waitTerminal(request: APIRequestContext, sid: string): Promise<any> {
  return waitFor(
    () => getSnapshot(request, sid),
    (snap) => snap?.status === 'completed' || snap?.status === 'failed',
    `会话 ${sid} 进入终态`,
  );
}