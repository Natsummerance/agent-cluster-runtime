import { beforeEach, describe, expect, it } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '../App';
import { useAppStore, apiErrorMessage } from '../store/appStore';
import { ApiError, setFetchImpl } from '../api/client';
import enUS from '../i18n/messages/en-US.json';
import zhCN from '../i18n/messages/zh-CN.json';
import { DEFAULT_LOCALE, MESSAGES } from '../i18n';

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function installApi() {
  setFetchImpl(async (input) => {
    const url = String(input);
    if (url.endsWith('/api/v1/status')) {
      return jsonResponse({
        ok: true,
        data: { version: '0.7.2', projects: 1, sessions: 0, active_sessions: 0, uptime: 60 },
      });
    }
    if (url.endsWith('/api/v1/projects')) {
      return jsonResponse({ ok: true, data: [] });
    }
    if (url.endsWith('/api/v1/metrics')) {
      return jsonResponse({ ok: true, data: null });
    }
    return jsonResponse({ ok: false, error: `unexpected: ${url}` }, 404);
  });
}

beforeEach(() => {
  localStorage.clear();
  useAppStore.getState().resetState();
  document.documentElement.lang = '';
});

describe('i18n', () => {
  it('en-US 与 zh-CN 消息 key 集合严格相等', () => {
    const en = Object.keys(enUS).sort();
    const zh = Object.keys(zhCN).sort();
    expect(en).toEqual(zh);
    expect(en.length).toBeGreaterThan(250);
  });

  it('locale 默认 zh-CN，切换后写入持久化存储', () => {
    expect(useAppStore.getState().locale).toBe(DEFAULT_LOCALE);
    useAppStore.getState().setLocale('en-US');
    expect(useAppStore.getState().locale).toBe('en-US');
    const raw = JSON.parse(localStorage.getItem('doai-workbench') ?? '{}') as {
      state?: { locale?: string };
    };
    expect(raw.state?.locale).toBe('en-US');
  });

  it('消息 key 命名符合 <page>.<component>.<token> 三段规范', () => {
    for (const id of Object.keys(enUS)) {
      if (id.startsWith('errors.')) {
        expect(id).toMatch(/^errors\.[a-z][a-z0-9_]*$/);
      } else {
        expect(id).toMatch(/^[a-z][A-Za-z0-9]*(\.[a-z][A-Za-z0-9]*){1,2}$/);
      }
    }
  });

  it('apiErrorMessage 按错误 code 查表，未知 code 原样显示后端文本', () => {
    useAppStore.getState().setLocale('zh-CN');
    const known = new ApiError('backend message', 403, {
      code: 'not_authorized',
      message: 'backend message',
    });
    expect(apiErrorMessage(known)).toBe(MESSAGES['zh-CN']['errors.not_authorized']);
    const unknown = new ApiError('raw backend text', 500, { code: 'mystery_code' });
    expect(apiErrorMessage(unknown)).toBe('raw backend text');
    useAppStore.getState().setLocale('en-US');
    expect(apiErrorMessage(known)).toBe(MESSAGES['en-US']['errors.not_authorized']);
  });

  it('设置页切换 en-US 后界面渲染英文并同步 documentElement.lang', async () => {
    installApi();
    window.history.pushState({}, '', '/settings');
    render(<App />);
    const select = await screen.findByTestId('settings-language-select');
    await userEvent.click(select.querySelector('.ant-select-selector') as HTMLElement);
    const option = await screen.findByTitle('en-US');
    await userEvent.click(option);
    await waitFor(() => expect(useAppStore.getState().locale).toBe('en-US'));
    await waitFor(() => expect(document.documentElement.lang).toBe('en-US'));
    expect(await screen.findByText('Server address')).toBeInTheDocument();
    expect(await screen.findByText('Dashboard')).toBeInTheDocument();
  });
});
