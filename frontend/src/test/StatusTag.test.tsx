import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import StatusTag from '../components/StatusTag';

describe('StatusTag', () => {
  it('running → 运行中', () => {
    render(<StatusTag status="running" />);
    expect(screen.getByTestId('status-tag')).toHaveTextContent('运行中');
  });

  it('waiting_approval → 等待审批', () => {
    render(<StatusTag status="waiting_approval" />);
    expect(screen.getByTestId('status-tag')).toHaveTextContent('等待审批');
  });

  it('completed → 已完成', () => {
    render(<StatusTag status="completed" />);
    expect(screen.getByTestId('status-tag')).toHaveTextContent('已完成');
  });

  it('failed → 失败', () => {
    render(<StatusTag status="failed" />);
    expect(screen.getByTestId('status-tag')).toHaveTextContent('失败');
  });

  it('未知状态原样显示', () => {
    render(<StatusTag status="paused" />);
    expect(screen.getByTestId('status-tag')).toHaveTextContent('paused');
  });
});