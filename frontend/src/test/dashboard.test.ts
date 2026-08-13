import { describe, expect, it } from 'vitest';
import { costAxis, healthAxis, progressAxis } from '../lib/dashboard';

describe('三轴计算式（§10.1）', () => {
  it('costAxis：500/1000 → ratio 0.5、score 0.5、ok', () => {
    const axis = costAxis(500, 1000, 0.8);
    expect(axis.used).toBe(500);
    expect(axis.limit).toBe(1000);
    expect(axis.ratio).toBeCloseTo(0.5);
    expect(axis.score).toBeCloseTo(0.5);
    expect(axis.status).toBe('ok');
  });

  it('costAxis：800/1000（≥0.8 预警比）→ warn', () => {
    expect(costAxis(800, 1000, 0.8).status).toBe('warn');
  });

  it('costAxis：1000/1000 → critical', () => {
    expect(costAxis(1000, 1000, 0.8).status).toBe('critical');
  });

  it('costAxis：limit=0 → ratio 0、ok', () => {
    const axis = costAxis(500, 0, 0.8);
    expect(axis.ratio).toBe(0);
    expect(axis.status).toBe('ok');
  });

  it('progressAxis：均值 0.7/0.4 三态边界（§10.1 ≥0.7 ok、≥0.4 warn、否则 critical）', () => {
    const phases = (done: number, pending: number) => [
      ...Array.from({ length: done }, () => ({ status: 'done' })),
      ...Array.from({ length: pending }, () => ({ status: 'pending' })),
    ];
    expect(progressAxis([{ phases: phases(7, 3) }]).score).toBeCloseTo(0.7);
    expect(progressAxis([{ phases: phases(7, 3) }]).status).toBe('ok');
    expect(progressAxis([{ phases: phases(2, 3) }]).score).toBeCloseTo(0.4);
    expect(progressAxis([{ phases: phases(2, 3) }]).status).toBe('warn');
    expect(progressAxis([{ phases: phases(1, 4) }]).status).toBe('critical');
    expect(progressAxis([{ phases: phases(0, 2) }]).status).toBe('critical');
  });

  it('progressAxis：phases 空 → completed=1 否则 0；无会话 → ok', () => {
    expect(progressAxis([{ status: 'completed', phases: [] }]).score).toBeCloseTo(1);
    expect(progressAxis([{ status: 'running', phases: [] }]).score).toBeCloseTo(0);
    expect(progressAxis([{ status: 'running', phases: [] }]).status).toBe('critical');
    expect(progressAxis([]).status).toBe('ok');
    expect(progressAxis([]).phases).toEqual({ total: 0, done: 0 });
  });

  it('healthAxis：None 指标跳过，均值三态', () => {
    const ok = healthAxis([{ session_id: 's1', health: { rework_rate: 0, estimate_accuracy: 0.9 } }]);
    expect(ok.status).toBe('ok');
    const warn = healthAxis([{ session_id: 's1', health: { rework_rate: 0.4 } }]);
    expect(warn.status).toBe('warn');
    const critical = healthAxis([{ session_id: 's1', health: { rework_rate: 0.9 } }]);
    expect(critical.status).toBe('critical');
  });

  it('healthAxis：over_budget 或 rework_rate≥0.5 → critical', () => {
    expect(healthAxis([{ session_id: 's1', over_budget: true, health: { rework_rate: 0 } }]).status).toBe('critical');
    expect(healthAxis([{ session_id: 's1', health: { rework_rate: 0.5 } }]).status).toBe('critical');
    expect(healthAxis([{ session_id: 's1', health: { rework_rate: 0.49 } }]).status).not.toBe('critical');
  });

  it('healthAxis：无会话 → ok 且 score 0', () => {
    const axis = healthAxis([]);
    expect(axis.status).toBe('ok');
    expect(axis.score).toBe(0);
  });
});
