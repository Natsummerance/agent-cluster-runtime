import { useMemo, useState } from 'react';
import { Alert, Button, Input, Modal, Space, Typography } from 'antd';
import { CheckOutlined, CloseOutlined, EditOutlined, MessageOutlined } from '@ant-design/icons';

export type GateTextMode = 'edit' | 'response';

interface GateApprovalModalProps {
  open: boolean;
  hint?: string | null;
  loading?: boolean;
  onAccept: () => void;
  onReject: () => void;
  onSubmitText: (mode: GateTextMode, text: string) => void;
  onCancel: () => void;
}

export default function GateApprovalModal({
  open,
  hint,
  loading = false,
  onAccept,
  onReject,
  onSubmitText,
  onCancel,
}: GateApprovalModalProps) {
  const [mode, setMode] = useState<GateTextMode | null>(null);
  const [text, setText] = useState('');

  const body = useMemo(() => {
    if (mode) {
      return (
        <Space direction="vertical" style={{ width: '100%' }}>
          <Typography.Text>
            {mode === 'edit' ? '编辑提案内容并提交给会话：' : '回复自由文本给会话：'}
          </Typography.Text>
          <Input.TextArea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={4}
            placeholder={mode === 'edit' ? '输入修改后的提案/需求内容…' : '输入你的回复…'}
            data-testid="gate-text-input"
          />
          <Space>
            <Button
              type="primary"
              disabled={!text.trim()}
              onClick={() => onSubmitText(mode, text.trim())}
              loading={loading}
              data-testid="gate-text-submit"
            >
              提交
            </Button>
            <Button onClick={() => setMode(null)}>返回</Button>
          </Space>
        </Space>
      );
    }
    return (
      <Alert
        type="warning"
        showIcon
        message="等待人工审批"
        description={hint ?? '（无提示信息）'}
        data-testid="gate-hint"
      />
    );
  }, [mode, text, hint, loading, onSubmitText]);

  return (
    <Modal
      open={open}
      title="审批门（HITL）"
      onCancel={onCancel}
      footer={null}
      width={560}
      destroyOnHidden
      data-testid="gate-modal"
    >
      {body}
      {!mode && (
        <Space style={{ marginTop: 16 }} wrap>
          <Button type="primary" icon={<CheckOutlined />} onClick={onAccept} loading={loading} data-testid="gate-accept">
            接受
          </Button>
          <Button danger icon={<CloseOutlined />} onClick={onReject} loading={loading} data-testid="gate-reject">
            拒绝
          </Button>
          <Button icon={<EditOutlined />} onClick={() => setMode('edit')} data-testid="gate-edit-mode">
            编辑文本
          </Button>
          <Button icon={<MessageOutlined />} onClick={() => setMode('response')} data-testid="gate-response-mode">
            回复文本
          </Button>
        </Space>
      )}
    </Modal>
  );
}