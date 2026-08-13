import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithIntl } from './renderWithIntl';
import TokenPanel from '../components/TokenPanel';
import type { TokenInfo } from '../api/types';

describe('TokenPanel', () => {
  const base: TokenInfo = { budget: 1000, used: 300, remaining: 700, over_budget: false };

  it('渲染预算/已用/剩余', () => {
    renderWithIntl(<TokenPanel token={base} />);
    expect(screen.getByTestId('token-panel')).toBeInTheDocument();
    expect(screen.getByText('预算')).toBeInTheDocument();
    expect(screen.getByText('已用')).toBeInTheDocument();
    expect(screen.getByText('剩余')).toBeInTheDocument();
  });

  it('over_budget 时显示超限告警', () => {
    renderWithIntl(<TokenPanel token={{ ...base, over_budget: true }} />);
    expect(screen.getByText(/Token 预算已超限/)).toBeInTheDocument();
  });

  it('渲染按阶段与按角色明细标签', () => {
    renderWithIntl(
      <TokenPanel
        token={{ ...base, by_phase: { 需求: 100, 开发: 200 }, by_role: { PM: 50 } }}
      />,
    );
    expect(screen.getAllByTestId('token-breakdown-tag')).toHaveLength(3);
  });

  it('token 为空时不渲染', () => {
    const { container } = renderWithIntl(<TokenPanel token={null} />);
    expect(container.firstChild).toBeNull();
  });

  it('预算为 0 时不产生 NaN 百分比', () => {
    renderWithIntl(<TokenPanel token={{ budget: 0, used: 0, remaining: 0, over_budget: false }} />);
    expect(screen.getByTestId('token-panel')).toBeInTheDocument();
  });
});