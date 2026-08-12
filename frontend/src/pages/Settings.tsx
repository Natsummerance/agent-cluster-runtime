import { useEffect, useState } from 'react';
import { Alert, Button, Card, Form, Input, message, Space, Switch, Typography } from 'antd';
import { CheckCircleOutlined, SaveOutlined } from '@ant-design/icons';
import { useAppStore, apiErrorMessage } from '../store/appStore';
import PageHeader from '../components/PageHeader';

export default function Settings() {
  const serverUrl = useAppStore((s) => s.serverUrl);
  const authToken = useAppStore((s) => s.authToken);
  const darkMode = useAppStore((s) => s.darkMode);
  const connected = useAppStore((s) => s.connected);
  const status = useAppStore((s) => s.status);
  const error = useAppStore((s) => s.error);
  const setServerUrl = useAppStore((s) => s.setServerUrl);
  const setAuthToken = useAppStore((s) => s.setAuthToken);
  const setDarkMode = useAppStore((s) => s.setDarkMode);
  const refreshStatus = useAppStore((s) => s.refreshStatus);
  const [form] = Form.useForm();
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    form.setFieldsValue({ serverUrl, authToken });
  }, [form, serverUrl, authToken]);

  const isValidationError = (err: unknown): boolean =>
    !!err && typeof err === 'object' && 'errorFields' in err;

  const save = async () => {
    try {
      const values = await form.validateFields();
      setServerUrl(values.serverUrl.trim());
      setAuthToken((values.authToken ?? '').trim());
      message.success('设置已保存（已写入本地存储）');
    } catch (err) {
      if (isValidationError(err)) return;
      message.error(apiErrorMessage(err));
    }
  };

  const testConnection = async () => {
    try {
      const values = await form.validateFields();
      setServerUrl(values.serverUrl.trim());
      setAuthToken((values.authToken ?? '').trim());
    } catch (err) {
      if (isValidationError(err)) return;
      message.error(apiErrorMessage(err));
      return;
    }
    setTesting(true);
    await refreshStatus();
    setTesting(false);
    if (useAppStore.getState().connected) {
      message.success(`连接成功，后端版本 ${useAppStore.getState().status?.version ?? '-'}`);
    } else {
      message.error(apiErrorMessage(useAppStore.getState().error));
    }
  };

  return (
    <div data-testid="settings-page" style={{ maxWidth: 640 }}>
      <PageHeader title="设置" description="后端连接与外观偏好（本地持久化）" />
      <Card className="page-card" title="后端连接">
        <Form form={form} layout="vertical" initialValues={{ serverUrl, authToken }}>
          <Form.Item
            name="serverUrl"
            label="服务器地址"
            rules={[
              { required: true, message: '请输入服务器地址' },
              {
                pattern: /^https?:\/\/.+/,
                message: '地址需以 http:// 或 https:// 开头',
              },
            ]}
          >
            <Input placeholder="http://127.0.0.1:8765" data-testid="server-url-input" />
          </Form.Item>
          <Form.Item name="authToken" label="认证令牌（X-Auth-Token，可选）">
            <Input.Password placeholder="留空表示无需认证" data-testid="auth-token-input" />
          </Form.Item>
          <Space>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              onClick={() => void save()}
              data-testid="save-settings-btn"
            >
              保存设置
            </Button>
            <Button
              icon={<CheckCircleOutlined />}
              loading={testing}
              onClick={() => void testConnection()}
              data-testid="test-connection-btn"
            >
              测试连接
            </Button>
          </Space>
        </Form>
        <div style={{ marginTop: 16 }}>
          {connected === false && (
            <Alert type="error" showIcon message="连接失败" description={error ?? '请确认后端已启动'} data-testid="settings-conn-error" />
          )}
          {connected === true && (
            <Alert type="success" showIcon message={`已连接（版本 ${status?.version ?? '-'}）`} data-testid="settings-conn-ok" />
          )}
        </div>
      </Card>
      <Card title="外观">
        <Space>
          <Typography.Text>深色模式</Typography.Text>
          <Switch checked={darkMode} onChange={setDarkMode} aria-label="深色模式" data-testid="settings-dark-switch" />
        </Space>
      </Card>
      <Typography.Paragraph type="secondary" style={{ marginTop: 16 }}>
        提示：设置通过 localStorage 持久化；请先在本机启动{' '}
        <Typography.Text code>agent-cluster serve</Typography.Text>（默认端口 8765）。
      </Typography.Paragraph>
    </div>
  );
}