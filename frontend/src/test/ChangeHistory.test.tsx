import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ChangeHistory from '../components/ChangeHistory';
import type { ChangeData } from '../api/types';

describe('ChangeHistory', () => {
  it('渲染变更概要', () => {
    render(<ChangeHistory data={{ records: [], summary: '共 0 条' }} onRollback={vi.fn()} />);
    expect(screen.getByText('共 0 条')).toBeInTheDocument();
  });

  it('渲染变更记录列表', () => {
    const data: ChangeData = {
      summary: '共 2 条',
      records: [
        { version: 1, ts: '2026-01-01T00:00:00', summary: '创建项目', type: 'create' },
        { version: 2, ts: '2026-01-02T00:00:00', summary: '添加登录', type: 'edit' },
      ],
    };
    render(<ChangeHistory data={data} onRollback={vi.fn()} />);
    const versions = screen.getAllByTestId('change-version');
    expect(versions).toHaveLength(2);
    expect(versions[0]).toHaveTextContent('v1');
    expect(versions[1]).toHaveTextContent('v2');
    expect(screen.getByText(/添加登录/)).toBeInTheDocument();
  });

  it('点击回滚按钮经确认后触发 onRollback', async () => {
    const onRollback = vi.fn();
    render(<ChangeHistory data={{ records: [{ version: 3, ts: 't', summary: 'x' }] }} onRollback={onRollback} />);
    await userEvent.click(screen.getByTestId('rollback-3'));
    const title = await screen.findByText('确定回滚到 3 吗？');
    const popover = title.closest('.ant-popover') as HTMLElement;
    expect(popover).not.toBeNull();
    await userEvent.click(within(popover).getByRole('button', { name: /回\s*滚/ }));
    expect(onRollback).toHaveBeenCalledWith(3);
  });

  it('无记录时显示空状态', () => {
    render(<ChangeHistory data={{ records: [] }} onRollback={vi.fn()} />);
    expect(screen.getByText('暂无变更记录')).toBeInTheDocument();
  });
});