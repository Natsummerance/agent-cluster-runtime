import { beforeEach, describe, expect, it } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithIntl } from './renderWithIntl';
import Dependencies from '../pages/Dependencies';
import { configureApi, setFetchImpl } from '../api/client';

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
}

function makeHarness() {
  const edges: Array<Record<string, unknown>> = [
    {
      id: 'e1',
      from_project: 'payments',
      to_project: 'ledger',
      from_task: 't1',
      to_task: 't2',
      type: 'build',
      created_at: '2026-08-13T00:00:00+00:00',
    },
    {
      id: 'e2',
      from_project: 'checkout',
      to_project: 'payments',
      from_task: '',
      to_task: '',
      type: 'runtime',
      created_at: '2026-08-13T00:00:00+00:00',
    },
  ];
  const lastGets: string[] = [];
  setFetchImpl(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/api/v1/dependencies/impact')) {
      lastGets.push(url);
      const query = new URL(url, 'http://127.0.0.1:8765').searchParams.get('project_id');
      const impacted = query === 'ledger' ? ['payments', 'checkout'] : [];
      return jsonResponse({ ok: true, data: { project_id: query ?? '', impact: impacted } });
    }
    if (url.includes('/api/v1/dependencies')) {
      if (method === 'GET') lastGets.push(url);
      if (method === 'POST') {
        const body = JSON.parse(String(init?.body));
        const item = { id: `new-${edges.length + 1}`, ...body, created_at: '2026-08-13T00:00:00+00:00' };
        edges.push(item);
        return jsonResponse({ ok: true, data: { edge: item } }, 201);
      }
      if (method === 'DELETE') {
        const id = decodeURIComponent(url.split('/').pop() ?? '');
        const index = edges.findIndex((e) => e.id === id);
        if (index >= 0) edges.splice(index, 1);
        return jsonResponse({ ok: true, data: { removed: id } });
      }
      return jsonResponse({ ok: true, data: { edges } });
    }
    return jsonResponse({ ok: false, error: 'not mocked' }, 404);
  });
  return { edges, lastGets };
}

beforeEach(() => {
  configureApi({ baseUrl: 'http://127.0.0.1:8765', authToken: null });
});

describe('Dependencies 页面（跨项目依赖图）', () => {
  it('渲染依赖表格、节点与连线概览', async () => {
    makeHarness();
    renderWithIntl(<Dependencies />);
    await waitFor(() => expect(screen.getByTestId('dependency-table')).toBeInTheDocument());
    const table = screen.getByTestId('dependency-table');
    await waitFor(() => expect(within(table).getAllByText('payments').length).toBeGreaterThan(0));
    expect(within(table).getByText('ledger')).toBeInTheDocument();
    expect(within(table).getByText('checkout')).toBeInTheDocument();
    expect(within(table).getByText('build')).toBeInTheDocument();
    expect(screen.getByTestId('dependency-node-payments')).toBeInTheDocument();
    expect(screen.getByTestId('dependency-node-ledger')).toBeInTheDocument();
    expect(screen.getByTestId('dependency-link-e1')).toBeInTheDocument();
    expect(screen.getByTestId('dependency-link-e2')).toBeInTheDocument();
  });

  it('新建依赖边提交 POST /api/v1/dependencies 并刷新表格', async () => {
    const harness = makeHarness();
    renderWithIntl(<Dependencies />);
    await waitFor(() => expect(screen.getByTestId('add-dependency-btn')).toBeEnabled());
    await userEvent.click(screen.getByTestId('add-dependency-btn'));
    await waitFor(() => expect(screen.getByTestId('create-dependency-modal')).toBeInTheDocument());
    await userEvent.type(screen.getByTestId('dependency-from-input'), 'billing');
    await userEvent.type(screen.getByTestId('dependency-to-input'), 'auth');
    await userEvent.type(screen.getByTestId('dependency-from-task-input'), 't9');
    await userEvent.type(screen.getByTestId('dependency-to-task-input'), 't10');
    fireEvent.mouseDown(screen.getByTestId('dependency-type-select').querySelector('.ant-select-selector')!);
    fireEvent.click(await screen.findByTitle('runtime'));
    await userEvent.click(
      screen.getByTestId('create-dependency-modal').querySelector('.ant-modal-footer .ant-btn-primary')!,
    );
    await waitFor(() => expect(harness.edges.some((e) => String(e.id).startsWith('new-'))).toBe(true));
    await waitFor(() =>
      expect(within(screen.getByTestId('dependency-table')).getByText('billing')).toBeInTheDocument(),
    );
    expect(harness.edges.find((e) => String(e.id).startsWith('new-'))?.type).toBe('runtime');
  });

  it('删除依赖边调用 DELETE /api/v1/dependencies/{id}', async () => {
    const harness = makeHarness();
    renderWithIntl(<Dependencies />);
    await waitFor(() => expect(screen.getByTestId('delete-dependency-e1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('delete-dependency-e1'));
    const title = await screen.findByText('确定删除该依赖边？');
    const popover = title.closest('.ant-popover') as HTMLElement;
    await userEvent.click(within(popover).getByRole('button', { name: /OK/i }));
    await waitFor(() => expect(harness.edges.some((e) => e.id === 'e1')).toBe(false));
    await waitFor(() => expect(screen.queryByText('t1')).not.toBeInTheDocument());
  });

  it('选择项目后请求影响分析并展示下游闭包', async () => {
    const harness = makeHarness();
    renderWithIntl(<Dependencies />);
    await waitFor(() => expect(screen.getByTestId('impact-project-select')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId('dependency-node-ledger')).toBeInTheDocument());
    fireEvent.mouseDown(screen.getByTestId('impact-project-select').querySelector('.ant-select-selector')!);
    fireEvent.click(await screen.findByTitle('ledger'));
    await waitFor(() => expect(screen.getByTestId('impact-tag-payments')).toBeInTheDocument());
    expect(screen.getByTestId('impact-tag-checkout')).toBeInTheDocument();
    expect(
      harness.lastGets.some((url) => url.includes('/api/v1/dependencies/impact') && url.includes('project_id=ledger')),
    ).toBe(true);
  });
});
