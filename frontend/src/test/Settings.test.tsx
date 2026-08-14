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

function doctorReport() {
  return {
    ok: false,
    checks: [
      { name: 'python', ok: true, required: true, detail: 'Python 3.11', action: '' },
      { name: 'docker', ok: false, required: true, detail: 'Docker 未安装', action: 'scripts/install-docker.ps1' },
      { name: 'model', ok: false, required: false, detail: '未指定模型', action: '' },
    ],
    fix: null,
  };
}

beforeEach(() => {
  localStorage.clear();
  useAppStore.getState().resetState();
  // 默认 mock：环境卡片挂载即拉取 /api/v1/doctor
  setFetchImpl(async (input) => {
    const url = String(input);
    if (url.includes('/api/v1/doctor')) return jsonResponse({ ok: true, data: doctorReport() });
    return jsonResponse({ ok: false, error: 'not mocked' }, 404);
  });
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
    setFetchImpl(async (input) => {
      const url = String(input);
      if (url.includes('/api/v1/status')) {
        return jsonResponse({ ok: true, data: { version: '0.7.0', projects: 0, sessions: 0, active_sessions: 0, uptime: 1 } });
      }
      return jsonResponse({ ok: true, data: doctorReport() });
    });
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

  it('环境卡片渲染预检报告与 Docker 修复指引 action', async () => {
    renderWithIntl(<Settings />);
    expect(await screen.findByTestId('env-check-docker')).toBeInTheDocument();
    expect(screen.getByText('scripts/install-docker.ps1')).toBeInTheDocument();
    expect(screen.getByTestId('env-check-python')).toBeInTheDocument();
  });

  it('一键修复按钮触发 POST /api/v1/doctor/fix-docker 并展示输出', async () => {
    const calls: string[] = [];
    setFetchImpl(async (input, init) => {
      const url = String(input);
      calls.push(`${init?.method ?? 'GET'} ${url}`);
      if (url.includes('/fix-docker')) {
        return jsonResponse({
          ok: true,
          data: {
            ...doctorReport(),
            ok: true,
            fix: { ran: true, exit_code: 0, output: 'installed ok' },
          },
        });
      }
      return jsonResponse({ ok: true, data: doctorReport() });
    });
    renderWithIntl(<Settings />);
    const button = await screen.findByTestId('env-fix-docker-btn');
    await userEvent.click(button);
    await waitFor(() =>
      expect(calls.some((c) => c.startsWith('POST') && c.includes('fix-docker'))).toBe(true),
    );
    expect(await screen.findByTestId('settings-env-fix-output')).toBeInTheDocument();
    expect(screen.getByText(/installed ok/)).toBeInTheDocument();
  });
});