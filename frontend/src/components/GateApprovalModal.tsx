import { useMemo, useState } from 'react';
import { Alert, Button, Input, Modal, Space, Typography } from 'antd';
import { CheckOutlined, CloseOutlined, EditOutlined, MessageOutlined } from '@ant-design/icons';
import { useIntl } from '../i18n';

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
  const intl = useIntl();
  const [mode, setMode] = useState<GateTextMode | null>(null);
  const [text, setText] = useState('');

  const body = useMemo(() => {
    if (mode) {
      return (
        <Space direction="vertical" style={{ width: '100%' }}>
          <Typography.Text>
            {mode === 'edit'
              ? intl.formatMessage({ id: 'gate.editPrompt', defaultMessage: 'Edit proposal and submit it to the session:' })
              : intl.formatMessage({ id: 'gate.responsePrompt', defaultMessage: 'Reply with free text to the session:' })}
          </Typography.Text>
          <Input.TextArea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={4}
            placeholder={
              mode === 'edit'
                ? intl.formatMessage({ id: 'gate.editPlaceholder', defaultMessage: 'Enter the revised proposal/requirements…' })
                : intl.formatMessage({ id: 'gate.responsePlaceholder', defaultMessage: 'Enter your reply…' })
            }
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
              {intl.formatMessage({ id: 'common.submit', defaultMessage: 'Submit' })}
            </Button>
            <Button onClick={() => setMode(null)}>
              {intl.formatMessage({ id: 'common.back', defaultMessage: 'Back' })}
            </Button>
          </Space>
        </Space>
      );
    }
    return (
      <Alert
        type="warning"
        showIcon
        message={intl.formatMessage({ id: 'gate.waiting', defaultMessage: 'Awaiting manual approval' })}
        description={hint ?? intl.formatMessage({ id: 'gate.noHint', defaultMessage: '(No hint)' })}
        data-testid="gate-hint"
      />
    );
  }, [mode, text, hint, loading, onSubmitText, intl]);

  return (
    <Modal
      open={open}
      title={intl.formatMessage({ id: 'gate.title', defaultMessage: 'Approval gate (HITL)' })}
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
            {intl.formatMessage({ id: 'gate.accept', defaultMessage: 'Accept' })}
          </Button>
          <Button danger icon={<CloseOutlined />} onClick={onReject} loading={loading} data-testid="gate-reject">
            {intl.formatMessage({ id: 'gate.reject', defaultMessage: 'Reject' })}
          </Button>
          <Button icon={<EditOutlined />} onClick={() => setMode('edit')} data-testid="gate-edit-mode">
            {intl.formatMessage({ id: 'gate.editMode', defaultMessage: 'Edit text' })}
          </Button>
          <Button icon={<MessageOutlined />} onClick={() => setMode('response')} data-testid="gate-response-mode">
            {intl.formatMessage({ id: 'gate.responseMode', defaultMessage: 'Reply text' })}
          </Button>
        </Space>
      )}
    </Modal>
  );
}
