import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { renderWithIntl } from './renderWithIntl';
import Audit from '../pages/Audit';
import { configureApi, setFetchImpl } from '../api/client';

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
}

const auditData = {
  ok: true,
  data: {
    session_id: 's1',
    goal: '审计目标',
    events: [
      { seq: 0, ts: '2026-08-14T10:00:00+00:00', type: 'session.start', actor: 'admin', payload: { goal: '审计目标' } },
      { seq: 1, ts: '2026-08-14T10:01:00+00:00', type: 'agent.response', actor: 'backend', payload: { text: '第一轮产出' } },
    ],
  },
};

const exportData = {
  ok: true,
  data: { session_id: 's1', format: 'csv', content: 'seq,ts,type\n0,2026-08-14T10:00:00+00:00,session.start\n' },
};

function makeHarness() {
  const calls: Array<{ url: string; method: string }> = [];
  setFetchImpl(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    calls.push({ url, method });
    if (url.includes('/audit/export')) return jsonResponse(exportData);
    if (url.includes('/audit')) return jsonResponse(auditData);
    return jsonResponse({ ok: false, error: 'not mocked' }, 404);
  });
  return calls;
}

function renderAudit() {
  return renderWithIntl(
    <MemoryRouter initialEntries={['/audit?session_id=s1']}>
      <Audit />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  configureApi({ baseUrl: 'http://127.0.0.1:8765', authToken: null });
});

describe('Audit 轨迹视图（T14.11）', () => {
  it('按事件时间线渲染轨迹（类型/seq/行为者/摘要）', async () => {
    makeHarness();
    renderAudit();
    await waitFor(() => expect(screen.getByTestId('audit-trajectory')).toBeInTheDocument());
    expect(screen.getByTestId('trajectory-item-0')).toBeInTheDocument();
    expect(screen.getByTestId('trajectory-item-1')).toBeInTheDocument();
    expect(screen.getByText('session.start')).toBeInTheDocument();
    expect(screen.getByText('agent.response')).toBeInTheDocument();
    expect(screen.getByText('第一轮产出')).toBeInTheDocument();
    expect(screen.getByText('backend')).toBeInTheDocument();
  });

  it('导出下拉请求 GET /audit/export?format=csv 并展示结果', async () => {
    const calls = makeHarness();
    renderAudit();
    await waitFor(() => expect(screen.getByTestId('audit-export-btn')).toBeEnabled());
    await userEvent.click(screen.getByTestId('audit-export-btn'));
    await userEvent.click(await screen.findByText('CSV'));
    await waitFor(() => expect(screen.getByTestId('audit-export-content')).toBeInTheDocument());
    const exportCall = calls.find((c) => c.url.includes('/audit/export'));
    expect(exportCall).toBeDefined();
    expect(exportCall!.method).toBe('GET');
    expect(exportCall!.url).toContain('format=csv');
    expect(screen.getByTestId('audit-export-content')).toHaveTextContent('seq,ts,type');
    expect(screen.getByTestId('audit-export-download')).toBeInTheDocument();
  });
});
