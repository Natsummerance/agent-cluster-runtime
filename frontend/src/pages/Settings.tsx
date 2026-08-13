import { useEffect, useState } from 'react';
import { Alert, Button, Card, Form, Input, message, Select, Space, Spin, Switch, Tag, Typography } from 'antd';
import { CheckCircleOutlined, ReloadOutlined, SaveOutlined } from '@ant-design/icons';
import { useAppStore, apiErrorMessage } from '../store/appStore';
import { fetchDoctor, fixDocker } from '../api/endpoints';
import type { DoctorReport } from '../api/types';
import { LOCALES } from '../i18n';
import type { Locale } from '../i18n';
import { useIntl } from '../i18n';
import PageHeader from '../components/PageHeader';

export default function Settings() {
  const intl = useIntl();
  const serverUrl = useAppStore((s) => s.serverUrl);
  const authToken = useAppStore((s) => s.authToken);
  const darkMode = useAppStore((s) => s.darkMode);
  const locale = useAppStore((s) => s.locale);
  const connected = useAppStore((s) => s.connected);
  const status = useAppStore((s) => s.status);
  const error = useAppStore((s) => s.error);
  const setServerUrl = useAppStore((s) => s.setServerUrl);
  const setAuthToken = useAppStore((s) => s.setAuthToken);
  const setDarkMode = useAppStore((s) => s.setDarkMode);
  const setLocale = useAppStore((s) => s.setLocale);
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
      message.success(
        intl.formatMessage({
          id: 'settings.saved',
          defaultMessage: 'Settings saved (written to local storage)',
        }),
      );
    } catch (err) {
      if (isValidationError(err)) return;
      message.error(apiErrorMessage(err));
    }
  };

  const [doctor, setDoctor] = useState<DoctorReport | null>(null);
  const [doctorLoading, setDoctorLoading] = useState(false);
  const [doctorError, setDoctorError] = useState<string | null>(null);
  const [fixingDocker, setFixingDocker] = useState(false);

  useEffect(() => {
    void loadDoctor();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadDoctor = async () => {
    setDoctorLoading(true);
    setDoctorError(null);
    try {
      setDoctor(await fetchDoctor());
    } catch (err) {
      setDoctorError(apiErrorMessage(err));
    } finally {
      setDoctorLoading(false);
    }
  };

  const fixDockerAction = async () => {
    setFixingDocker(true);
    try {
      const report = await fixDocker();
      setDoctor(report);
      if (report.ok) {
        message.success(
          intl.formatMessage({ id: 'settings.envFixDone', defaultMessage: 'Docker fix finished, checks re-run' }),
        );
      }
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setFixingDocker(false);
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
      message.success(
        intl.formatMessage(
          { id: 'settings.testOk', defaultMessage: 'Connection OK, backend version {version}' },
          { version: useAppStore.getState().status?.version ?? '-' },
        ),
      );
    } else {
      message.error(apiErrorMessage(useAppStore.getState().error));
    }
  };

  return (
    <div data-testid="settings-page" style={{ maxWidth: 640 }}>
      <PageHeader
        title={intl.formatMessage({ id: 'settings.header.title', defaultMessage: 'Settings' })}
        description={intl.formatMessage({
          id: 'settings.header.desc',
          defaultMessage: 'Backend connection and appearance preferences (persisted locally)',
        })}
      />
      <Card className="page-card" title={intl.formatMessage({ id: 'settings.connCard', defaultMessage: 'Backend connection' })}>
        <Form form={form} layout="vertical" initialValues={{ serverUrl, authToken }}>
          <Form.Item
            name="serverUrl"
            label={intl.formatMessage({ id: 'settings.serverUrl', defaultMessage: 'Server address' })}
            rules={[
              {
                required: true,
                message: intl.formatMessage({
                  id: 'settings.serverUrlRequired',
                  defaultMessage: 'Please enter the server address',
                }),
              },
              {
                pattern: /^https?:\/\/.+/,
                message: intl.formatMessage({
                  id: 'settings.serverUrlPattern',
                  defaultMessage: 'Address must start with http:// or https://',
                }),
              },
            ]}
          >
            <Input placeholder="http://127.0.0.1:8765" data-testid="server-url-input" />
          </Form.Item>
          <Form.Item
            name="authToken"
            label={intl.formatMessage({
              id: 'settings.authToken',
              defaultMessage: 'Auth token (X-Auth-Token, optional)',
            })}
          >
            <Input.Password
              placeholder={intl.formatMessage({
                id: 'settings.authTokenPlaceholder',
                defaultMessage: 'Leave empty for no authentication',
              })}
              data-testid="auth-token-input"
            />
          </Form.Item>
          <Space>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              onClick={() => void save()}
              data-testid="save-settings-btn"
            >
              {intl.formatMessage({ id: 'settings.save', defaultMessage: 'Save settings' })}
            </Button>
            <Button
              icon={<CheckCircleOutlined />}
              loading={testing}
              onClick={() => void testConnection()}
              data-testid="test-connection-btn"
            >
              {intl.formatMessage({ id: 'settings.test', defaultMessage: 'Test connection' })}
            </Button>
          </Space>
        </Form>
        <div style={{ marginTop: 16 }}>
          {connected === false && (
            <Alert
              type="error"
              showIcon
              message={intl.formatMessage({ id: 'layout.connectionFailed', defaultMessage: 'Connection failed' })}
              description={
                error ??
                intl.formatMessage({
                  id: 'settings.connErrorHint',
                  defaultMessage: 'Please confirm the backend is running',
                })
              }
              data-testid="settings-conn-error"
            />
          )}
          {connected === true && (
            <Alert
              type="success"
              showIcon
              message={intl.formatMessage(
                { id: 'settings.connectedVersion', defaultMessage: 'Connected (version {version})' },
                { version: status?.version ?? '-' },
              )}
              data-testid="settings-conn-ok"
            />
          )}
        </div>
      </Card>
      <Card
        title={intl.formatMessage({ id: 'settings.appearanceCard', defaultMessage: 'Appearance' })}
        style={{ marginTop: 16 }}
      >
        <Space direction="vertical" size="middle">
          <Space>
            <Typography.Text>
              {intl.formatMessage({ id: 'common.darkMode', defaultMessage: 'Dark mode' })}
            </Typography.Text>
            <Switch
              checked={darkMode}
              onChange={setDarkMode}
              aria-label={intl.formatMessage({ id: 'common.darkMode', defaultMessage: 'Dark mode' })}
              data-testid="settings-dark-switch"
            />
          </Space>
          <Space>
            <Typography.Text>
              {intl.formatMessage({ id: 'settings.language', defaultMessage: 'Language' })}
            </Typography.Text>
            <Select
              value={locale}
              onChange={(value) => setLocale(value as Locale)}
              options={LOCALES.map((value) => ({ value, label: value }))}
              style={{ width: 140 }}
              aria-label={intl.formatMessage({ id: 'settings.language', defaultMessage: 'Language' })}
              data-testid="settings-language-select"
            />
          </Space>
        </Space>
      </Card>
      <Card
        title={intl.formatMessage({ id: 'settings.envCard', defaultMessage: 'Environment' })}
        style={{ marginTop: 16 }}
        data-testid="settings-env-card"
      >
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Typography.Text type="secondary">
            {intl.formatMessage({ id: 'settings.envDesc', defaultMessage: 'Runtime environment checks' })}
          </Typography.Text>
          {doctorLoading && !doctor ? <Spin /> : null}
          {!doctorLoading && !doctor && !doctorError ? (
            <Typography.Text type="secondary">
              {intl.formatMessage({ id: 'settings.envNoReport', defaultMessage: 'No doctor report yet' })}
            </Typography.Text>
          ) : null}
          {doctorError && !doctor ? (
            <Alert
              type="error"
              showIcon
              message={doctorError}
              data-testid="settings-env-error"
            />
          ) : null}
          {doctor && Array.isArray(doctor.checks) ? (
            <>
              {doctor.checks.map((check) => (
                <div key={check.name} data-testid={`env-check-${check.name}`}>
                  <Space align="baseline" wrap>
                    <Tag color={check.ok ? 'green' : check.required ? 'red' : 'orange'}>
                      {check.ok
                        ? intl.formatMessage({ id: 'settings.envPassed', defaultMessage: 'Passed' })
                        : check.required
                          ? intl.formatMessage({ id: 'settings.envFailed', defaultMessage: 'Failed' })
                          : intl.formatMessage({ id: 'settings.envWarned', defaultMessage: 'Warning' })}
                    </Tag>
                    <Typography.Text strong>{check.name}</Typography.Text>
                    <Typography.Text type="secondary">{check.detail}</Typography.Text>
                  </Space>
                  {check.action ? (
                    <div style={{ marginTop: 4 }}>
                      <Typography.Text type="secondary">
                        {intl.formatMessage({ id: 'settings.envAction', defaultMessage: 'Fix action' })}:{' '}
                      </Typography.Text>
                      <Typography.Text code>{check.action}</Typography.Text>
                    </div>
                  ) : null}
                </div>
              ))}
              {doctor.fix ? (
                <Alert
                  type={doctor.fix.exit_code === 0 ? 'success' : 'warning'}
                  showIcon
                  message={intl.formatMessage({ id: 'settings.envFixOutput', defaultMessage: 'Fix output' })}
                  description={doctor.fix.output || '-'}
                  data-testid="settings-env-fix-output"
                />
              ) : null}
              <Space>
                <Button
                  icon={<ReloadOutlined />}
                  loading={doctorLoading}
                  onClick={() => void loadDoctor()}
                  data-testid="env-refresh-btn"
                >
                  {intl.formatMessage({ id: 'settings.envRefresh', defaultMessage: 'Re-run checks' })}
                </Button>
                <Button
                  type="primary"
                  danger
                  loading={fixingDocker}
                  disabled={doctor.ok}
                  onClick={() => void fixDockerAction()}
                  data-testid="env-fix-docker-btn"
                >
                  {intl.formatMessage({
                    id: fixingDocker ? 'settings.envFixing' : 'settings.envFix',
                    defaultMessage: 'Fix Docker',
                  })}
                </Button>
              </Space>
            </>
          ) : null}
        </Space>
      </Card>
      <Typography.Paragraph type="secondary" style={{ marginTop: 16 }}>
        {intl.formatMessage({
          id: 'settings.tip',
          defaultMessage: 'Settings are persisted via localStorage; start agent-cluster serve on this machine first (default port 8765).',
        })}{' '}
        <Typography.Text code>agent-cluster serve</Typography.Text>
      </Typography.Paragraph>
    </div>
  );
}
