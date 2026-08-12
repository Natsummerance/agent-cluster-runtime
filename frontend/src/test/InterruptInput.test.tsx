import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import InterruptInput from '../components/InterruptInput';

describe('InterruptInput', () => {
  it('输入文本并提交调用 onInterrupt', async () => {
    const onInterrupt = vi.fn().mockResolvedValue(undefined);
    render(<InterruptInput onInterrupt={onInterrupt} />);
    await userEvent.type(screen.getByTestId('interrupt-text'), '改成邮箱登录');
    await userEvent.click(screen.getByTestId('interrupt-submit'));
    expect(onInterrupt).toHaveBeenCalledWith('改成邮箱登录');
  });

  it('提交后清空输入框', async () => {
    const onInterrupt = vi.fn().mockResolvedValue(undefined);
    render(<InterruptInput onInterrupt={onInterrupt} />);
    await userEvent.type(screen.getByTestId('interrupt-text'), 'abc');
    await userEvent.click(screen.getByTestId('interrupt-submit'));
    expect(screen.getByTestId('interrupt-text')).toHaveValue('');
  });

  it('空白输入时提交按钮禁用', () => {
    render(<InterruptInput onInterrupt={vi.fn()} />);
    expect(screen.getByTestId('interrupt-submit')).toBeDisabled();
  });

  it('disabled 时禁止输入与提交', () => {
    render(<InterruptInput disabled onInterrupt={vi.fn()} />);
    expect(screen.getByTestId('interrupt-text')).toBeDisabled();
    expect(screen.getByTestId('interrupt-submit')).toBeDisabled();
  });
});