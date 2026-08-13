import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithIntl } from './renderWithIntl';
import userEvent from '@testing-library/user-event';
import GateApprovalModal from '../components/GateApprovalModal';

describe('GateApprovalModal', () => {
  it('显示审批提示文本', () => {
    renderWithIntl(
      <GateApprovalModal
        open
        hint="请确认需求评审通过"
        onAccept={vi.fn()}
        onReject={vi.fn()}
        onSubmitText={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByTestId('gate-hint')).toHaveTextContent('请确认需求评审通过');
  });

  it('点击“接受”触发 onAccept', async () => {
    const onAccept = vi.fn();
    renderWithIntl(
      <GateApprovalModal open hint="h" onAccept={onAccept} onReject={vi.fn()} onSubmitText={vi.fn()} onCancel={vi.fn()} />,
    );
    await userEvent.click(screen.getByTestId('gate-accept'));
    expect(onAccept).toHaveBeenCalledTimes(1);
  });

  it('点击“拒绝”触发 onReject', async () => {
    const onReject = vi.fn();
    renderWithIntl(
      <GateApprovalModal open hint="h" onAccept={vi.fn()} onReject={onReject} onSubmitText={vi.fn()} onCancel={vi.fn()} />,
    );
    await userEvent.click(screen.getByTestId('gate-reject'));
    expect(onReject).toHaveBeenCalledTimes(1);
  });

  it('编辑文本模式提交带文本', async () => {
    const onSubmitText = vi.fn();
    renderWithIntl(
      <GateApprovalModal open hint="h" onAccept={vi.fn()} onReject={vi.fn()} onSubmitText={onSubmitText} onCancel={vi.fn()} />,
    );
    await userEvent.click(screen.getByTestId('gate-edit-mode'));
    await userEvent.type(screen.getByTestId('gate-text-input'), '修改后的内容');
    await userEvent.click(screen.getByTestId('gate-text-submit'));
    expect(onSubmitText).toHaveBeenCalledWith('edit', '修改后的内容');
  });

  it('回复文本模式提交带文本', async () => {
    const onSubmitText = vi.fn();
    renderWithIntl(
      <GateApprovalModal open hint="h" onAccept={vi.fn()} onReject={vi.fn()} onSubmitText={onSubmitText} onCancel={vi.fn()} />,
    );
    await userEvent.click(screen.getByTestId('gate-response-mode'));
    await userEvent.type(screen.getByTestId('gate-text-input'), '我的回复');
    await userEvent.click(screen.getByTestId('gate-text-submit'));
    expect(onSubmitText).toHaveBeenCalledWith('response', '我的回复');
  });

  it('空白文本时提交按钮禁用', async () => {
    renderWithIntl(
      <GateApprovalModal open hint="h" onAccept={vi.fn()} onReject={vi.fn()} onSubmitText={vi.fn()} onCancel={vi.fn()} />,
    );
    await userEvent.click(screen.getByTestId('gate-edit-mode'));
    expect(screen.getByTestId('gate-text-submit')).toBeDisabled();
  });

  it('未打开时不渲染内容', () => {
    const { container } = renderWithIntl(
      <GateApprovalModal open={false} hint="h" onAccept={vi.fn()} onReject={vi.fn()} onSubmitText={vi.fn()} onCancel={vi.fn()} />,
    );
    expect(container.querySelector('[data-testid="gate-modal"]')).not.toBeInTheDocument();
  });
});