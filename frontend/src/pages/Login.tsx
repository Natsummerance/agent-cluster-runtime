import { useState } from 'react';
import { Button, Card, Form, Input, Typography, message } from 'antd';
import { LockOutlined, UserOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { apiErrorMessage, useAppStore } from '../store/appStore';
import { useIntl } from '../i18n';

export default function Login() {
  const intl = useIntl();
  const navigate = useNavigate();
  const login = useAppStore((s) => s.login);
  const serverUrl = useAppStore((s) => s.serverUrl);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (values: { username: string; password: string }) => {
    setSubmitting(true);
    setError(null);
    try {
      const user = await login(values.username, values.password);
      message.success(intl.formatMessage({ id: 'login.success', defaultMessage: 'Signed in as {user}' }, { user }));
      navigate('/', { replace: true });
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}
      data-testid="login-page"
    >
      <Card style={{ width: 380 }} title={intl.formatMessage({ id: 'login.title', defaultMessage: 'Sign in' })}>
        <Typography.Paragraph type="secondary" data-testid="login-server-url">
          {serverUrl}
        </Typography.Paragraph>
        <Form layout="vertical" onFinish={handleSubmit}>
          <Form.Item
            name="username"
            label={intl.formatMessage({ id: 'login.username', defaultMessage: 'Username' })}
            rules={[{ required: true, message: intl.formatMessage({ id: 'login.usernameRequired', defaultMessage: 'Please enter the username' }) }]}
          >
            <Input prefix={<UserOutlined />} autoComplete="username" data-testid="login-username-input" />
          </Form.Item>
          <Form.Item
            name="password"
            label={intl.formatMessage({ id: 'login.password', defaultMessage: 'Password' })}
            rules={[{ required: true, message: intl.formatMessage({ id: 'login.passwordRequired', defaultMessage: 'Please enter the password' }) }]}
          >
            <Input.Password prefix={<LockOutlined />} autoComplete="current-password" data-testid="login-password-input" />
          </Form.Item>
          {error && (
            <Typography.Text type="danger" data-testid="login-error">
              {error}
            </Typography.Text>
          )}
          <Button type="primary" htmlType="submit" block loading={submitting} style={{ marginTop: 16 }} data-testid="login-submit-btn">
            {intl.formatMessage({ id: 'login.submit', defaultMessage: 'Sign in' })}
          </Button>
        </Form>
      </Card>
    </div>
  );
}
