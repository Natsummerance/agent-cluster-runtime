import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { renderWithIntl } from './renderWithIntl';
import Login from '../pages/Login';
import { useAppStore } from '../store/appStore';
import { configureApi, setFetchImpl } from '../api/client';

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
}

const tokens = { user: 'alice', access_token: 'access-abc', refresh_token: 'refresh-xyz' };

beforeEach(() => {
  localStorage.clear();
  configureApi({ baseUrl: 'http://127.0.0.1:8765', authToken: null });
  useAppStore.getState().resetState();
});

describe('Login 页面（认证）', () => {
  it('登录成功写入 access/refresh token 并注入 Bearer', async () => {
    setFetchImpl(async (input, init) => {
      const url = String(input);
      if (url.includes('/api/v1/auth/login') && init?.method === 'POST') {
        return jsonResponse({ ok: true, data: tokens });
      }
      return jsonResponse({ ok: false, error: 'not mocked' }, 404);
    });
    renderWithIntl(
      <MemoryRouter>
        <Login />
      </MemoryRouter>,
    );
    await userEvent.type(screen.getByTestId('login-username-input'), 'alice');
    await userEvent.type(screen.getByTestId('login-password-input'), 'pw-1');
    await userEvent.click(screen.getByTestId('login-submit-btn'));
    await waitFor(() => {
      expect(useAppStore.getState().accessToken).toBe('access-abc');
    });
    expect(useAppStore.getState().refreshToken).toBe('refresh-xyz');
    expect(useAppStore.getState().authUser).toBe('alice');
    expect(useAppStore.getState().authEnabled).toBe(true);
  });

  it('登录失败展示错误且不写入 token', async () => {
    setFetchImpl(async () => jsonResponse({ ok: false, error: '用户名或密码错误' }, 401));
    renderWithIntl(
      <MemoryRouter>
        <Login />
      </MemoryRouter>,
    );
    await userEvent.type(screen.getByTestId('login-username-input'), 'alice');
    await userEvent.type(screen.getByTestId('login-password-input'), 'bad');
    await userEvent.click(screen.getByTestId('login-submit-btn'));
    await waitFor(() => expect(screen.getByTestId('login-error')).toBeInTheDocument());
    expect(useAppStore.getState().accessToken).toBe('');
  });

  it('API 客户端对 Bearer token 使用 Authorization 头（X-Auth-Token 不受影响）', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true, data: { user: 'alice' } })) as Mock;
    setFetchImpl(fetchMock);
    configureApi({ baseUrl: 'http://127.0.0.1:8765', authToken: 'Bearer access-abc' });
    const { apiRequest } = await import('../api/client');
    await apiRequest('/api/v1/auth/me');
    const init = fetchMock.mock.calls[0]?.[1] as unknown as RequestInit | undefined;
    const headers = init?.headers as Record<string, string> | undefined;
    expect(headers?.['Authorization']).toBe('Bearer access-abc');
    expect(headers?.['X-Auth-Token']).toBeUndefined();
    configureApi({ baseUrl: 'http://127.0.0.1:8765', authToken: 'legacy-token' });
    await apiRequest('/api/v1/users');
    const headers2 = fetchMock.mock.calls[1]?.[1] as unknown as { headers?: Record<string, string> } | undefined;
    expect(headers2?.headers?.['X-Auth-Token']).toBe('legacy-token');
  });
});
