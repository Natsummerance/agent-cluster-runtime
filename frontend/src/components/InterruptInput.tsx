import { useState } from 'react';
import { Button, Input, Space, Typography } from 'antd';
import { PauseCircleOutlined } from '@ant-design/icons';

interface InterruptInputProps {
  disabled?: boolean;
  loading?: boolean;
  onInterrupt: (text: string) => Promise<void> | void;
}

export default function InterruptInput({ disabled = false, loading = false, onInterrupt }: InterruptInputProps) {
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
        实时打断：向运行中的会话发送新指示（例如调整需求、变更优先级）。
      </Typography.Text>
      <Space.Compact style={{ width: '100%' }}>
        <Input.TextArea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="输入打断指令，例如：把登录模块改为邮箱验证…"
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
          发送打断
        </Button>
      </Space.Compact>
    </Space>
  );
}