import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithIntl } from './renderWithIntl';
import Users from '../pages/Users';
import Teams from '../pages/Teams';
import { configureApi, setFetchImpl } from '../api/client';

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
}

const roleData = {
  ok: true,
  data: {
    roles: [
      { id: 'backend', name: '后端开发工程师', kind: 'backend', permissions: ['project.read', 'project.write'] },
      { id: 'governance', name: '治理', kind: 'pm', permissions: ['users.manage'] },
    ],
  },
};

function makeHarness() {
  const users: Array<Record<string, unknown>> = [
    { id: 'admin', name: '系统管理员', role_ids: ['governance'], scopes: ['*'], is_admin: true },
  ];
  const teams: Array<Record<string, unknown>> = [{ id: 't-web', name: '前端组', member_ids: ['admin'] }];
  setFetchImpl(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/api/v1/roles')) return jsonResponse(roleData);
    if (url.includes('/api/v1/users')) {
      if (method === 'POST') {
        const body = JSON.parse(String(init?.body));
        users.push({ id: body.id, name: body.name, role_ids: body.role_ids ?? [], scopes: body.scopes ?? [] });
        return jsonResponse({ ok: true, data: { user: users[users.length - 1] } }, 201);
      }
      return jsonResponse({ ok: true, data: { users } });
    }
    if (url.includes('/api/v1/teams')) {
      if (method === 'POST') {
        const body = JSON.parse(String(init?.body));
        teams.push({ id: body.id, name: body.name, member_ids: [] });
        return jsonResponse({ ok: true, data: { team: teams[teams.length - 1] } }, 201);
      }
      return jsonResponse({ ok: true, data: { teams } });
    }
    return jsonResponse({ ok: false, error: 'not mocked' }, 404);
  });
  return { users, teams };
}

beforeEach(() => {
  configureApi({ baseUrl: 'http://127.0.0.1:8765', authToken: null });
});

describe('Users 页面（RBAC）', () => {
  it('渲染用户表与岗位信息', async () => {
    makeHarness();
    renderWithIntl(<Users />);
    await waitFor(() => expect(screen.getByTestId('users-table')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText('系统管理员')).toBeInTheDocument());
    expect(screen.getByText('治理')).toBeInTheDocument();
  });

  it('新建用户提交 POST /api/v1/users 并刷新列表', async () => {
    const harness = makeHarness();
    renderWithIntl(<Users />);
    await waitFor(() => expect(screen.getByTestId('add-user-btn')).toBeEnabled());
    await userEvent.click(screen.getByTestId('add-user-btn'));
    await userEvent.type(screen.getByTestId('user-id-input'), 'dev-9');
    await userEvent.type(screen.getByTestId('user-name-input'), '开发九');
    await userEvent.click(screen.getByTestId('create-user-modal').querySelector('.ant-modal-footer .ant-btn-primary')!);
    await waitFor(() => expect(harness.users.some((u) => u.id === 'dev-9')).toBe(true));
    await waitFor(() => expect(screen.getByText('开发九')).toBeInTheDocument());
  });
});

describe('Teams 页面（RBAC）', () => {
  it('渲染团队表与成员', async () => {
    makeHarness();
    renderWithIntl(<Teams />);
    await waitFor(() => expect(screen.getByTestId('teams-table')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText('前端组')).toBeInTheDocument());
    expect(screen.getByText('系统管理员')).toBeInTheDocument();
  });

  it('新建团队提交 POST /api/v1/teams 并刷新列表', async () => {
    const harness = makeHarness();
    renderWithIntl(<Teams />);
    await waitFor(() => expect(screen.getByTestId('add-team-btn')).toBeEnabled());
    await userEvent.click(screen.getByTestId('add-team-btn'));
    await userEvent.type(screen.getByTestId('team-id-input'), 't-mobile');
    await userEvent.type(screen.getByTestId('team-name-input'), '移动组');
    await userEvent.click(screen.getByTestId('create-team-modal').querySelector('.ant-modal-footer .ant-btn-primary')!);
    await waitFor(() => expect(harness.teams.some((t) => t.id === 't-mobile')).toBe(true));
    await waitFor(() => expect(screen.getByText('移动组')).toBeInTheDocument());
  });
});
