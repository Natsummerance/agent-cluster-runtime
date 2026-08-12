// E2E mock 后端：用 page.route 拦截 /api/**，不依赖真实 serve
import type { Page, Route } from '@playwright/test';

export function envelope(data: unknown, ok = true) {
  return ok ? { ok: true, data } : { ok: false, error: typeof data === 'string' ? data : '请求失败' };
}

export const mockStatus = {
  version: '0.5.0',
  projects: 2,
  sessions: 3,
  active_sessions: 1,
  uptime: 3661,
};

export const mockProjects = [
  { id: 'p1', name: '待办应用', workspace: 'ws/todo', status: 'active', created_at: '2026-08-01T08:00:00' },
  { id: 'p2', name: '博客系统', workspace: 'ws/blog', status: 'active', created_at: '2026-08-02T09:00:00' },
];

export function makeSession(overrides: Record<string, unknown> = {}) {
  return {
    session_id: 's1',
    project_id: 'p1',
    workspace: 'ws/todo',
    goal: '构建待办事项 Web 应用',
    model: 'codex',
    status: 'running',
    pending_hint: null,
    current_phase: '开发',
    current_node: 'dev',
    token: { budget: 100000, used: 12345, remaining: 87655, over_budget: false, by_phase: { 需求: 3000, 开发: 9345 }, by_role: { PM: 2000, DEV: 10345 } },
    phases: ['需求', '设计', '开发'],
    transcript_count: 5,
    gate_count: 1,
    health: { eval_pass_rate_trend: [0.7, 0.8], token_cost: 0.12, estimate_accuracy: 0.85, rework_rate: 0.1 },
    error: null,
    exit_code: null,
    ...overrides,
  };
}

export const mockChanges = {
  summary: '共 3 条变更',
  records: [
    { version: 1, ts: '2026-08-01T08:05:00', summary: '初始化项目结构', type: 'create' },
    { version: 2, ts: '2026-08-01T08:20:00', summary: '实现任务 CRUD', type: 'edit' },
    { version: 3, ts: '2026-08-01T08:40:00', summary: '添加登录页', type: 'edit' },
  ],
};

export const mockMemory = {
  items: [
    { id: 'mem-1', content: '项目使用 React + AntD 技术栈', tags: ['tech'], created_at: '2026-08-01T08:00:00' },
    { id: 'mem-2', content: '登录方式确定使用邮箱验证码', tags: ['auth'], created_at: '2026-08-01T08:10:00' },
  ],
  proposals: [
    { id: 'pro-1', content: '建议引入测试覆盖率门禁', tags: ['proposal'], created_at: '2026-08-01T09:00:00' },
  ],
};

export const mockProposals = [
  { id: 'e1', title: '引入审批超时自动降级', summary: '审批超时自动降级为跳过', status: 'applied', evidence: '3 次超时事件', created_at: '2026-08-01T09:00:00' },
  { id: 'e2', title: '增加阶段产物检查', summary: '阶段结束前检查产物清单', status: 'proposed', evidence: '2 次返工', created_at: '2026-08-01T10:00:00' },
];

export const mockPlugins = [
  { name: 'codex-hooks', description: 'Codex 风格插件钩子' },
  { name: 'docker-sandbox', description: 'Docker 沙箱执行' },
];
export const mockSkills = [
  { name: 'frontend', description: '前端开发技能包' },
  { name: 'testing', description: '测试技能包' },
];

export interface MockState {
  sessions: Record<string, Record<string, unknown>>;
  createdProjects: { name: string; workspace: string }[];
  promoted: string[];
  approved: string[];
  interrupted: string[];
  rolledBack: string[];
}

export function createState(): MockState {
  return {
    sessions: {
      s1: makeSession(),
      s2: makeSession({ session_id: 's2', goal: '重构登录模块', status: 'waiting_approval', pending_hint: '请确认登录改造方案（邮箱验证码）' }),
      s3: makeSession({ session_id: 's3', project_id: 'p2', goal: '博客系统 MVP', status: 'completed', current_phase: '发布', exit_code: 0 }),
    },
    createdProjects: [],
    promoted: [],
    approved: [],
    interrupted: [],
    rolledBack: [],
  };
}

function sseBody(): Buffer {
  return Buffer.from(
    [
      'data: {"seq":1,"type":"session_start","ts":"2026-08-01T08:00:00Z","data":{"text":"会话已启动"}}\n\n',
      'data: {"seq":2,"type":"phase_start","ts":"2026-08-01T08:01:00Z","data":{"phase":"需求评审"}}\n\n',
      'data: {"seq":3,"type":"gate","ts":"2026-08-01T08:02:00Z","data":{"hint":"请确认里程碑"}}\n\n',
    ].join(''),
    'utf8',
  );
}

export interface MockOptions {
  failAll?: boolean;
  failStatus?: boolean;
  projectError?: string;
  withAuthToken?: string;
}

export async function installApiMocks(page: Page, opts: MockOptions = {}) {
  const state = createState();

  const fail = async (route: Route) => {
    if (opts.failAll) {
      await route.abort('connectionrefused');
      return true;
    }
    return false;
  };

  await page.route('**/api/v1/status', async (route) => {
    if (await fail(route)) return;
    if (opts.failStatus) return route.fulfill({ status: 500, body: 'boom' });
    return route.fulfill({ json: envelope(mockStatus) });
  });

  await page.route('**/api/v1/metrics', async (route) => {
    if (await fail(route)) return;
    return route.fulfill({
      json: envelope({ sessions: 3, active: 1, total_tokens: 45678, total_cost: 0.42, health: { eval_pass_rate_trend: [0.7, 0.8] }, updated_at: '2026-08-01T09:00:00' }),
    });
  });

  await page.route('**/api/v1/projects', async (route) => {
    if (await fail(route)) return;
    if (route.request().method() === 'POST') {
      if (opts.projectError) {
        return route.fulfill({ status: 400, json: envelope(opts.projectError, false) });
      }
      const body = route.request().postDataJSON() as { name: string; workspace: string };
      state.createdProjects.push(body);
      return route.fulfill({
        status: 201,
        json: envelope({ id: `p${state.createdProjects.length + 10}`, name: body.name, workspace: body.workspace, status: 'active', created_at: '2026-08-01T09:00:00' }),
      });
    }
    const created = state.createdProjects.map((item, index) => ({
      id: `p${index + 11}`,
      name: item.name,
      workspace: item.workspace,
      status: 'active',
      created_at: '2026-08-01T09:00:00',
    }));
    return route.fulfill({ json: envelope([...mockProjects, ...created]) });
  });

  await page.route('**/api/v1/projects/*/sessions', async (route) => {
    if (await fail(route)) return;
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      const sid = `s${Date.now()}`;
      state.sessions[sid] = makeSession({ session_id: sid, goal: String(body.goal ?? ''), model: body.model ?? 'codex' });
      return route.fulfill({ status: 201, json: envelope({ session_id: sid, project_id: 'p1', workspace: 'ws/todo' }) });
    }
    const projectId = /projects\/([^/]+)\/sessions/.exec(route.request().url())?.[1];
    const list = Object.values(state.sessions).filter((s) => !projectId || s.project_id === projectId);
    return route.fulfill({ json: envelope(list) });
  });

  await page.route('**/api/v1/sessions/*', async (route) => {
    if (await fail(route)) return;
    const sid = /\/sessions\/([^/]+)$/.exec(new URL(route.request().url()).pathname)?.[1] ?? '';
    const snapshot = state.sessions[sid];
    if (!snapshot) {
      return route.fulfill({ status: 404, json: envelope(`会话 ${sid} 不存在`, false) });
    }
    return route.fulfill({ json: envelope(snapshot) });
  });
  await page.route('**/api/v1/sessions/*/events*', async (route) => {
    if (await fail(route)) return;
    return route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      headers: { 'Cache-Control': 'no-cache' },
      body: sseBody(),
    });
  });

  await page.route('**/api/v1/sessions/*/changes', async (route) => {
    if (await fail(route)) return;
    return route.fulfill({ json: envelope(mockChanges) });
  });

  await page.route('**/api/v1/sessions/*/approve', async (route) => {
    if (await fail(route)) return;
    const sid = /sessions\/([^/]+)\/approve/.exec(route.request().url())?.[1] ?? '';
    state.approved.push(sid);
    state.sessions[sid] = { ...state.sessions[sid], status: 'running', pending_hint: null };
    return route.fulfill({ json: envelope({ ok: true }) });
  });

  await page.route('**/api/v1/sessions/*/reject', async (route) => {
    if (await fail(route)) return;
    const sid = /sessions\/([^/]+)\/reject/.exec(route.request().url())?.[1] ?? '';
    state.sessions[sid] = { ...state.sessions[sid], status: 'failed', pending_hint: null };
    return route.fulfill({ json: envelope({ ok: true }) });
  });

  for (const action of ['edit', 'response']) {
    await page.route(`**/api/v1/sessions/*/${action}`, async (route) => {
      if (await fail(route)) return;
      const sid = new RegExp(`sessions\\/([^/]+)\\/${action}`).exec(route.request().url())?.[1] ?? '';
      state.sessions[sid] = { ...state.sessions[sid], status: 'running', pending_hint: null };
      return route.fulfill({ json: envelope({ ok: true }) });
    });
  }

  await page.route('**/api/v1/sessions/*/interrupt', async (route) => {
    if (await fail(route)) return;
    state.interrupted.push(JSON.parse(route.request().postData() ?? '{}').text ?? '');
    return route.fulfill({ status: 202, json: envelope({ ok: true }) });
  });

  await page.route('**/api/v1/sessions/*/rollback', async (route) => {
    if (await fail(route)) return;
    state.rolledBack.push(String(route.request().postDataJSON().version));
    return route.fulfill({ json: envelope({ ok: true }) });
  });

  await page.route('**/api/v1/sessions/*/audit**', async (route) => {
    if (await fail(route)) return;
    if (route.request().method() === 'POST') {
      return route.fulfill({ json: envelope({ file: 'audit-s1.json', session_id: 's1' }) });
    }
    return route.fulfill({
      json: envelope({ session_id: 's1', summary: '审计摘要', records: [{ event: 'approve', ts: '2026-08-01T08:30:00' }] }),
    });
  });

  await page.route('**/api/v1/projects/*/workspace/tree*', async (route) => {
    if (await fail(route)) return;
    const url = new URL(route.request().url());
    const path = url.searchParams.get('path') ?? '';
    if (!path) {
      return route.fulfill({ json: envelope({ path: '', entries: [{ name: 'src', type: 'dir', size: 0 }, { name: 'README.md', type: 'file', size: 1024 }] }) });
    }
    if (path === 'src') {
      return route.fulfill({ json: envelope({ path: 'src', entries: [{ name: 'App.tsx', type: 'file', size: 2048 }, { name: 'components', type: 'dir', size: 0 }] }) });
    }
    return route.fulfill({ json: envelope({ path, entries: [] }) });
  });

  await page.route('**/api/v1/projects/*/workspace/file*', async (route) => {
    if (await fail(route)) return;
    const url = new URL(route.request().url());
    const path = url.searchParams.get('path') ?? '';
    return route.fulfill({
      json: envelope({ path, file: { name: path.split('/').pop(), size: 2048, content: "import React from 'react';\nexport default function App() { return <div>hi</div>; }\n", mime: 'text/plain' } }),
    });
  });

  await page.route('**/api/v1/projects/*/memory', async (route) => {
    if (await fail(route)) return;
    return route.fulfill({ json: envelope(mockMemory) });
  });

  await page.route('**/api/v1/memory/*/promote', async (route) => {
    if (await fail(route)) return;
    state.promoted.push(/memory\/([^/]+)\/promote/.exec(route.request().url())?.[1] ?? '');
    return route.fulfill({ json: envelope({ ok: true }) });
  });

  await page.route('**/api/v1/plugins', async (route) => {
    if (await fail(route)) return;
    return route.fulfill({ json: envelope(mockPlugins) });
  });
  await page.route('**/api/v1/skills', async (route) => {
    if (await fail(route)) return;
    return route.fulfill({ json: envelope(mockSkills) });
  });
  await page.route('**/api/v1/mcp', async (route) => {
    if (await fail(route)) return;
    return route.fulfill({ json: envelope({ note: 'MCP 集成待配置，占位返回' }) });
  });

  await page.route('**/api/v1/evolution/proposals*', async (route) => {
    if (await fail(route)) return;
    return route.fulfill({ json: envelope(mockProposals) });
  });
  await page.route('**/api/v1/evolution/generate', async (route) => {
    if (await fail(route)) return;
    return route.fulfill({ json: envelope({ generated: 2, proposals: mockProposals }) });
  });
  await page.route('**/api/v1/evolution/proposals/*/apply', async (route) => {
    if (await fail(route)) return;
    return route.fulfill({ json: envelope({ ok: true }) });
  });
  await page.route('**/api/v1/evolution/proposals/*/rollback', async (route) => {
    if (await fail(route)) return;
    return route.fulfill({ json: envelope({ ok: true }) });
  });
  await page.route('**/api/v1/evolution/retro', async (route) => {
    if (await fail(route)) return;
    return route.fulfill({ json: envelope({ report: '复盘完成', actions: 3 }) });
  });

  return { state };
}