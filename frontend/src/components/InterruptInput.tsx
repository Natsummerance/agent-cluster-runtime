import { useState } from 'react';
import { Button, Input, Space, Typography } from 'antd';
import { PauseCircleOutlined } from '@ant-design/icons';
import { useIntl } from '../i18n';

interface InterruptInputProps {
  disabled?: boolean;
  loading?: boolean;
  onInterrupt: (text: string) => Promise<void> | void;
}

export default function InterruptInput({ disabled = false, loading = false, onInterrupt }: InterruptInputProps) {
  const intl = useIntl();
  const [text, setText] = useState('');

  const submit = async () => {
    const value = text.trim();
    if (!value) return;
    await onInterrupt(value);
    setText('');
  };

  return (
    <Space direction="vertical" style={{ width: '100%' }} data-testid="interrupt-input">
      <Typography.Text type="secondary">
        {intl.formatMessage({
          id: 'interrupt.help',
          defaultMessage:
            'Interrupt now: send new instructions to a running session (e.g. adjust requirements, reprioritize).',
        })}
      </Typography.Text>
      <Space.Compact style={{ width: '100%' }}>
        <Input.TextArea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={intl.formatMessage({
            id: 'interrupt.placeholder',
            defaultMessage: 'Enter interrupt instruction, e.g. change the login module to email verification…',
          })}
          autoSize={{ minRows: 1, maxRows: 3 }}
          disabled={disabled}
          data-testid="interrupt-text"
        />
        <Button
          type="primary"
          icon={<PauseCircleOutlined />}
          onClick={submit}
          loading={loading}
          disabled={disabled || !text.trim()}
          data-testid="interrupt-submit"
        >
          {intl.formatMessage({ id: 'interrupt.submit', defaultMessage: 'Send interrupt' })}
        </Button>
      </Space.Compact>
    </Space>
  );
}
