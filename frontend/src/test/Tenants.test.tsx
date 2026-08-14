import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithIntl } from './renderWithIntl';
import Tenants from '../pages/Tenants';
import { configureApi, setFetchImpl } from '../api/client';

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
}

function makeHarness() {
  const tenants: Array<Record<string, unknown>> = [
    { id: 'acme', name: 'Acme 租户', project_limit: 5, session_limit: 10, created_at: '2026-08-14T00:00:00+00:00' },
  ];
  const usage: Record<string, Record<string, unknown>> = {
    acme: { projects: 1, sessions: 2, project_limit: 5, session_limit: 10 },
  };
  setFetchImpl(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    const match = url.match(/\/api\/v1\/tenants\/([^/]+)\/usage/);
    if (match) {
      const tid = decodeURIComponent(match[1]);
      return jsonResponse({ ok: true, data: { usage: usage[tid] } });
    }
    if (url.includes('/api/v1/tenants')) {
      if (method === 'POST') {
        const body = JSON.parse(String(init?.body));
        tenants.push({ id: body.id, name: body.name, project_limit: body.project_limit ?? 0, session_limit: body.session_limit ?? 0 });
        usage[body.id] = { projects: 0, sessions: 0, project_limit: body.project_limit ?? 0, session_limit: body.session_limit ?? 0 };
        return jsonResponse({ ok: true, data: { tenant: tenants[tenants.length - 1] } }, 201);
      }
      if (method === 'DELETE') {
        const tid = decodeURIComponent(url.split('/').pop() ?? '');
        const index = tenants.findIndex((t) => t.id === tid);
        if (index >= 0) tenants.splice(index, 1);
        delete usage[tid];
        return jsonResponse({ ok: true, data: { removed: tid } });
      }
      return jsonResponse({ ok: true, data: { tenants } });
    }
    return jsonResponse({ ok: false, error: 'not mocked' }, 404);
  });
  return { tenants, usage };
}

beforeEach(() => {
  configureApi({ baseUrl: 'http://127.0.0.1:8765', authToken: null });
});

describe('Tenants 页面（多租户）', () => {
  it('渲染租户表与用量', async () => {
    makeHarness();
    renderWithIntl(<Tenants />);
    await waitFor(() => expect(screen.getByTestId('tenants-table')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText('Acme 租户')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText('1/5')).toBeInTheDocument());
    expect(screen.getByText('2/10')).toBeInTheDocument();
  });

  it('新建租户提交 POST /api/v1/tenants 并刷新列表', async () => {
    const harness = makeHarness();
    renderWithIntl(<Tenants />);
    await waitFor(() => expect(screen.getByTestId('add-tenant-btn')).toBeEnabled());
    await userEvent.click(screen.getByTestId('add-tenant-btn'));
    await userEvent.type(screen.getByTestId('tenant-id-input'), 'beta');
    await userEvent.type(screen.getByTestId('tenant-name-input'), 'Beta 租户');
    await userEvent.click(screen.getByTestId('create-tenant-modal').querySelector('.ant-modal-footer .ant-btn-primary')!);
    await waitFor(() => expect(harness.tenants.some((t) => t.id === 'beta')).toBe(true));
    await waitFor(() => expect(screen.getByText('Beta 租户')).toBeInTheDocument());
  });

  it('删除租户调用 DELETE /api/v1/tenants/{id}', async () => {
    const harness = makeHarness();
    renderWithIntl(<Tenants />);
    await waitFor(() => expect(screen.getByTestId('delete-tenant-acme')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('delete-tenant-acme'));
    const title = await screen.findByText(/Remove this tenant/);
    const popover = title.closest('.ant-popover') as HTMLElement;
    await userEvent.click(within(popover).getByRole('button', { name: /OK/i }));
    await waitFor(() => expect(harness.tenants.some((t) => t.id === 'acme')).toBe(false));
  });
});
