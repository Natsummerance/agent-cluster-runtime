import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithIntl } from './renderWithIntl';
import StatusTag from '../components/StatusTag';

describe('StatusTag', () => {
  it('running → 运行中', () => {
    renderWithIntl(<StatusTag status="running" />);
    expect(screen.getByTestId('status-tag')).toHaveTextContent('运行中');
  });

  it('waiting_approval → 等待审批', () => {
    renderWithIntl(<StatusTag status="waiting_approval" />);
    expect(screen.getByTestId('status-tag')).toHaveTextContent('等待审批');
  });

  it('completed → 已完成', () => {
    renderWithIntl(<StatusTag status="completed" />);
    expect(screen.getByTestId('status-tag')).toHaveTextContent('已完成');
  });

  it('failed → 失败', () => {
    renderWithIntl(<StatusTag status="failed" />);
    expect(screen.getByTestId('status-tag')).toHaveTextContent('失败');
  });

  it('未知状态原样显示', () => {
    renderWithIntl(<StatusTag status="paused" />);
    expect(screen.getByTestId('status-tag')).toHaveTextContent('paused');
  });
});