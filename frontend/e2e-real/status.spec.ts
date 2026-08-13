import { test, expect } from '@playwright/test';
import { api } from './helpers/api';

test.describe('真实后端 status', () => {
  test('GET /api/v1/status 返回版本与计数', async ({ request }) => {
    const res = await api(request, 'GET', '/api/v1/status');
    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
    expect(res.body.data.version).toMatch(/^\d+\.\d+\.\d+$/);
    expect(typeof res.body.data.projects).toBe('number');
    expect(typeof res.body.data.sessions).toBe('number');
    expect(typeof res.body.data.active_sessions).toBe('number');
    expect(typeof res.body.data.uptime).toBe('string');
    expect(res.body.data.uptime.length).toBeGreaterThan(0);
  });

  test('缺少 X-Auth-Token 返回 401 not_authorized', async ({ playwright }) => {
    const ctx = await playwright.request.newContext({ baseURL: 'http://127.0.0.1:8765' });
    const res = await ctx.get('/api/v1/status');
    expect(res.status()).toBe(401);
    const body = await res.json();
    expect(body.ok).toBe(false);
    expect(body.code).toBe('not_authorized');
    await ctx.dispose();
  });

  test('未知路由保持 v0.5 旧式信封（无 code）', async ({ request }) => {
    const res = await api(request, 'GET', '/api/v1/unknown-route');
    expect(res.status).toBe(404);
    expect(res.body.ok).toBe(false);
    expect(res.body.code).toBeUndefined();
    expect(typeof res.body.error).toBe('string');
  });
});