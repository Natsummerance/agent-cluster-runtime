import { beforeEach, describe, expect, it } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { renderWithIntl } from './renderWithIntl';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import SessionDetail from '../pages/SessionDetail';
import { useSessionStore } from '../store/sessionStore';
import { setFetchImpl } from '../api/client';

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function snapshot(status: string) {
  return {
    ok: true,
    data: {
      session_id: 's1',
      project_id: 'p1',
      workspace: 'ws/todo',
      goal: '构建待办事项 Web 应用',
      model: 'codex',
      status,
      pending_hint: null,
      current_phase: '开发',
      current_node: 'dev',
      token: { budget: 100000, used: 12345, remaining: 87655, over_budget: false },
      phases: ['需求', '开发'],
      transcript_count: 1,
      gate_count: 0,
      health: null,
      error: null,
      exit_code: null,
    },
  };
}

function installFetch(status = 'running') {
  const stdinCalls: { text: string }[] = [];
  setFetchImpl(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (method === 'POST' && url.endsWith('/api/v1/sessions/s1/stdin')) {
      stdinCalls.push(JSON.parse(String(init?.body ?? '{}')) as { text: string });
      return jsonResponse({ ok: true, data: { accepted: stdinCalls.at(-1)?.text ?? '' } }, 202);
    }
    if (url.endsWith('/api/v1/sessions/s1/changes')) {
      return jsonResponse({ ok: true, data: { summary: '共 0 条变更', records: [] } });
    }
    if (url.endsWith('/api/v1/sessions/s1')) {
      return jsonResponse(snapshot(status));
    }
    return jsonResponse({ ok: false, error: `未预期请求：${url}` }, 404);
  });
  return stdinCalls;
}

function renderPage() {
  return renderWithIntl(
    <MemoryRouter initialEntries={['/projects/p1/sessions/s1']}>
      <Routes>
        <Route path="/projects/:pid/sessions/:sid" element={<SessionDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  useSessionStore.getState().resetState();
});

describe('SessionDetail 实时输入', () => {
  it('运行中会话渲染可用输入框', async () => {
    installFetch('running');
    renderPage();
    expect(await screen.findByTestId('stdin-card')).toBeInTheDocument();
    expect(screen.getByTestId('stdin-text')).toBeEnabled();
    expect(screen.getByTestId('stdin-submit')).toBeDisabled();
  });

  it('终态会话禁用输入框与提交', async () => {
    installFetch('completed');
    renderPage();
    expect(await screen.findByTestId('stdin-text')).toBeDisabled();
    expect(screen.getByTestId('stdin-submit')).toBeDisabled();
  });

  it('提交调用 stdin 端点且成功后清空', async () => {
    const stdinCalls = installFetch('running');
    renderPage();
    const input = await screen.findByTestId('stdin-text');
    await userEvent.type(input, '补充：支持导出');
    await userEvent.click(screen.getByTestId('stdin-submit'));
    await waitFor(() => expect(stdinCalls).toHaveLength(1));
    expect(stdinCalls[0]).toEqual({ text: '补充：支持导出' });
    expect(screen.getByTestId('stdin-text')).toHaveValue('');
    expect(await screen.findByText('实时输入已注入')).toBeInTheDocument();
  });
});
