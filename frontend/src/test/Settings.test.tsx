import { beforeEach, describe, expect, it } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { renderWithIntl } from './renderWithIntl';
import userEvent from '@testing-library/user-event';
import Settings from '../pages/Settings';
import { useAppStore } from '../store/appStore';
import { setFetchImpl } from '../api/client';

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
}

beforeEach(() => {
  localStorage.clear();
  useAppStore.getState().resetState();
});

describe('Settings 页面', () => {
  it('渲染服务器地址与令牌输入框', () => {
    renderWithIntl(<Settings />);
    expect(screen.getByTestId('server-url-input')).toBeInTheDocument();
    expect(screen.getByTestId('auth-token-input')).toBeInTheDocument();
  });

  it('保存设置写入 store 并持久化到 localStorage', async () => {
    renderWithIntl(<Settings />);
    await userEvent.clear(screen.getByTestId('server-url-input'));
    await userEvent.type(screen.getByTestId('server-url-input'), 'http://127.0.0.1:9000');
    await userEvent.clear(screen.getByTestId('auth-token-input'));
    await userEvent.type(screen.getByTestId('auth-token-input'), 'my-token');
    await userEvent.click(screen.getByTestId('save-settings-btn'));
    expect(useAppStore.getState().serverUrl).toBe('http://127.0.0.1:9000');
    expect(useAppStore.getState().authToken).toBe('my-token');
    const raw = localStorage.getItem('agent-cluster-workbench');
    expect(raw).toContain('http://127.0.0.1:9000');
    expect(raw).toContain('my-token');
  });

  it('测试连接成功显示成功提示', async () => {
    setFetchImpl(async () => jsonResponse({ ok: true, data: { version: '0.5.0', projects: 0, sessions: 0, active_sessions: 0, uptime: 1 } }));
    renderWithIntl(<Settings />);
    await userEvent.click(screen.getByTestId('test-connection-btn'));
    await waitFor(() => expect(useAppStore.getState().connected).toBe(true));
    expect(await screen.findByTestId('settings-conn-ok')).toBeInTheDocument();
  });

  it('测试连接失败显示错误提示', async () => {
    setFetchImpl(async () => {
      throw new TypeError('Failed to fetch');
    });
    renderWithIntl(<Settings />);
    await userEvent.click(screen.getByTestId('test-connection-btn'));
    await waitFor(() => expect(useAppStore.getState().connected).toBe(false));
    expect(await screen.findByTestId('settings-conn-error')).toBeInTheDocument();
  });

  it('非法地址校验阻止保存', async () => {
    renderWithIntl(<Settings />);
    await userEvent.clear(screen.getByTestId('server-url-input'));
    await userEvent.type(screen.getByTestId('server-url-input'), 'not-a-url');
    await userEvent.click(screen.getByTestId('save-settings-btn'));
    expect(await screen.findByText(/地址需以 http/)).toBeInTheDocument();
    expect(useAppStore.getState().serverUrl).not.toBe('not-a-url');
  });

  it('深色模式开关切换 darkMode', async () => {
    renderWithIntl(<Settings />);
    await userEvent.click(screen.getByTestId('settings-dark-switch'));
    expect(useAppStore.getState().darkMode).toBe(true);
  });
});