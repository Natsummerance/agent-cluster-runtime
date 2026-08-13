import { test, expect } from '@playwright/test';
import { mkdtempSync, mkdirSync, writeFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { api, expectOk, makeWorkspace, uniqueSuffix } from './helpers/api';

test.describe('真实后端 projects', () => {
  test('创建项目：详情字段契约与 workspace 注册', async ({ request }) => {
    const name = `e2e-projects-${uniqueSuffix()}`;
    const workspace = makeWorkspace();
    const created = expectOk(
      await api(request, 'POST', '/api/v1/projects', { name, workspace }),
      201,
      '创建项目',
    );
    expect(created.id).toMatch(/^[0-9a-f]{12}$/);

    const detail = expectOk(await api(request, 'GET', `/api/v1/projects/${created.id}`), 200, '项目详情');
    expect(detail.project_id).toBe(created.id);
    expect(detail.name).toBe(name);
    expect(detail.workspaces).toContain(workspace);
    expect(detail.gate_policy.auto_review).toBe(true);
    for (const key of ['budget_pool', 'sessions', 'metadata']) {
      expect(detail).toHaveProperty(key);
    }

    const listed = expectOk(await api(request, 'GET', '/api/v1/projects'), 200, '项目列表');
    const entry = listed.find((item: any) => item.id === created.id);
    expect(entry).toBeTruthy();
    for (const key of ['budget_pool', 'session_count', 'active_sessions', 'dashboard']) {
      expect(entry).toHaveProperty(key);
    }
    expect(Object.keys(entry.dashboard).sort()).toEqual(['cost', 'health', 'progress', 'updated_at']);

    // PATCH 改名 + 非法 gate_policy → 400
    const patched = expectOk(
      await api(request, 'PATCH', `/api/v1/projects/${created.id}`, { name: `${name}-改` }),
      200,
      '改名',
    );
    expect(patched.name).toBe(`${name}-改`);
    const bad = await api(request, 'PATCH', `/api/v1/projects/${created.id}`, {
      gate_policy: { review_confidence_threshold: 1.7 },
    });
    expect(bad.status).toBe(400);
    expect(bad.body.code).toBe('bad_request');

    // workspaces 追加 + 不存在路径 400
    const extra = mkdtempSync(join(tmpdir(), 'acr-e2e-extra-'));
    const withExtra = expectOk(
      await api(request, 'POST', `/api/v1/projects/${created.id}/workspaces`, { path: extra }),
      200,
      '追加 workspace',
    );
    expect(withExtra.workspaces).toHaveLength(2);
    const missing = await api(request, 'POST', `/api/v1/projects/${created.id}/workspaces`, {
      path: join(tmpdir(), 'acr-e2e-missing-xxx'),
    });
    expect(missing.status).toBe(400);
    expect(missing.body.code).toBe('bad_request');
  });

  test('v0.5 遗留 session.json 自动迁移为项目首个会话', async ({ request }) => {
    const suffix = uniqueSuffix();
    const legacySid = `v05-${suffix}`;
    const workspace = mkdtempSync(join(tmpdir(), 'acr-e2e-legacy-'));
    const agentDir = join(workspace, '.agent-cluster');
    mkdirSync(agentDir, { recursive: true });
    writeFileSync(
      join(agentDir, 'session.json'),
      JSON.stringify(
        {
          session_id: legacySid,
          thread_id: `t:legacy-${suffix}`,
          goal: '遗留待办应用',
          status: 'completed',
          workspace,
        },
        null,
        2,
      ),
      'utf8',
    );

    const created = expectOk(
      await api(request, 'POST', '/api/v1/projects', { name: `迁移项目-${suffix}`, workspace }),
      201,
      '创建含遗留数据的项目',
    );
    const detail = expectOk(await api(request, 'GET', `/api/v1/projects/${created.id}`), 200, '项目详情（迁移后）');
    expect(
      detail.sessions.some((s: any) => s.session_id === legacySid && s.status === 'completed'),
    ).toBe(true);

    const tasks = expectOk(await api(request, 'GET', `/api/v1/projects/${created.id}/tasks`), 200, '任务列表');
    expect(tasks.some((t: any) => t.session_id === legacySid)).toBe(true);

    // 迁移标记落盘（幂等）
    expect(existsSync(join(agentDir, '.migrated.json'))).toBe(true);
    const budget = expectOk(await api(request, 'GET', `/api/v1/projects/${created.id}/budget`), 200, '预算快照');
    expect(typeof budget.used).toBe('number');
  });
});