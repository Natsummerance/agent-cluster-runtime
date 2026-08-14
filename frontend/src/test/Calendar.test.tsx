import { beforeEach, describe, expect, it } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithIntl } from './renderWithIntl';
import Calendar from '../pages/Calendar';
import { configureApi, setFetchImpl } from '../api/client';

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
}

function makeHarness() {
  const availability: Array<Record<string, unknown>> = [
    {
      id: 'a1',
      role_id: 'backend',
      start: '2026-08-14T09:00:00+00:00',
      end: '2026-08-14T12:00:00+00:00',
      note: '上午联调',
      created_at: '2026-08-13T00:00:00+00:00',
    },
    {
      id: 'a2',
      role_id: 'qa',
      start: '2026-08-15T09:00:00+00:00',
      end: '2026-08-15T11:00:00+00:00',
      note: '',
      created_at: '2026-08-13T00:00:00+00:00',
    },
  ];
  const roles = [
    { id: 'backend', name: '后端开发工程师', kind: 'backend', permissions: ['project.read'] },
    { id: 'qa', name: '测试工程师', kind: 'qa', permissions: ['project.read'] },
  ];
  const lastGets: string[] = [];
  setFetchImpl(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/api/v1/roles')) {
      return jsonResponse({ ok: true, data: { roles } });
    }
    if (url.includes('/api/v1/calendar')) {
      if (method === 'GET') lastGets.push(url);
      if (method === 'POST') {
        const body = JSON.parse(String(init?.body));
        const item = { id: `new-${availability.length + 1}`, ...body, created_at: '2026-08-13T00:00:00+00:00' };
        availability.push(item);
        return jsonResponse({ ok: true, data: { availability: item } }, 201);
      }
      if (method === 'DELETE') {
        const id = decodeURIComponent(url.split('/').pop() ?? '');
        const index = availability.findIndex((a) => a.id === id);
        if (index >= 0) availability.splice(index, 1);
        return jsonResponse({ ok: true, data: { removed: id } });
      }
      return jsonResponse({ ok: true, data: { availability } });
    }
    return jsonResponse({ ok: false, error: 'not mocked' }, 404);
  });
  return { availability, roles, lastGets };
}

beforeEach(() => {
  configureApi({ baseUrl: 'http://127.0.0.1:8765', authToken: null });
});

describe('Calendar 页面（资源日历）', () => {
  it('渲染日历网格、岗位筛选与可用块表格', async () => {
    makeHarness();
    renderWithIntl(<Calendar />);
    await waitFor(() => expect(screen.getByTestId('availability-table')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText('上午联调')).toBeInTheDocument());
    expect(within(screen.getByTestId('availability-table')).getByText('测试工程师')).toBeInTheDocument();
    expect(screen.getByTestId('availability-tag-a2')).toBeInTheDocument();
    expect(screen.getByTestId('calendar-grid')).toBeInTheDocument();
    expect(screen.getByTestId('calendar-role-filter')).toBeInTheDocument();
  });

  it('按岗位筛选后重新请求 GET /api/v1/calendar?role_id=...', async () => {
    const harness = makeHarness();
    renderWithIntl(<Calendar />);
    await waitFor(() => expect(screen.getByTestId('calendar-role-filter')).toBeInTheDocument());
    await waitFor(() =>
      expect(within(screen.getByTestId('availability-table')).getByText('后端开发工程师')).toBeInTheDocument(),
    );
    fireEvent.mouseDown(screen.getByTestId('calendar-role-filter').querySelector('.ant-select-selector')!);
    fireEvent.click(await screen.findByTitle('后端开发工程师'));
    await waitFor(() => expect(harness.lastGets.some((url) => url.includes('role_id=backend'))).toBe(true));
  });

  it('新建可用块提交 POST /api/v1/calendar 并刷新表格', async () => {
    const harness = makeHarness();
    renderWithIntl(<Calendar />);
    await waitFor(() => expect(screen.getByTestId('add-availability-btn')).toBeEnabled());
    await userEvent.click(screen.getByTestId('add-availability-btn'));
    await waitFor(() =>
      expect(within(screen.getByTestId('availability-table')).getByText('测试工程师')).toBeInTheDocument(),
    );
    fireEvent.mouseDown(screen.getByTestId('availability-role-select').querySelector('.ant-select-selector')!);
    fireEvent.click(await screen.findByTitle('测试工程师'));
    const modal = screen.getByTestId('create-availability-modal');
    const inputs = within(modal).getAllByRole('textbox'); // [start, end, note]
    fireEvent.change(inputs[0], { target: { value: '2026-08-16 09:00' } });
    fireEvent.keyDown(inputs[0], { key: 'Enter', code: 'Enter' });
    fireEvent.change(inputs[1], { target: { value: '2026-08-16 12:00' } });
    fireEvent.keyDown(inputs[1], { key: 'Enter', code: 'Enter' });
    await userEvent.type(screen.getByTestId('availability-note-input'), '回归测试');
    await userEvent.click(
      screen.getByTestId('create-availability-modal').querySelector('.ant-modal-footer .ant-btn-primary')!,
    );
    await waitFor(() => expect(harness.availability.some((a) => String(a.id).startsWith('new-'))).toBe(true));
    await waitFor(() => expect(screen.getByText('回归测试')).toBeInTheDocument());
  });

  it('删除可用块调用 DELETE /api/v1/calendar/{id}', async () => {
    const harness = makeHarness();
    renderWithIntl(<Calendar />);
    await waitFor(() => expect(screen.getByTestId('delete-availability-a1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('delete-availability-a1'));
    const title = await screen.findByText(/Remove this availability/);
    const popover = title.closest('.ant-popover') as HTMLElement;
    await userEvent.click(within(popover).getByRole('button', { name: /OK/i }));
    await waitFor(() => expect(harness.availability.some((a) => a.id === 'a1')).toBe(false));
    await waitFor(() => expect(screen.queryByText('上午联调')).not.toBeInTheDocument());
  });
});