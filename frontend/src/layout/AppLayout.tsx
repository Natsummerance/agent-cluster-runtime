import { useEffect, useMemo } from 'react';
import { Alert, Badge, Button, Layout, Menu, Space, Switch, Tooltip, Typography, theme as antdTheme } from 'antd';
import {
  ApiOutlined,
  AuditOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  FolderOutlined,
  FolderOpenOutlined,
  MoonOutlined,
  ReloadOutlined,
  SettingOutlined,
  SunOutlined,
  ApartmentOutlined,
  CalendarOutlined,
  ShareAltOutlined,
  ScheduleOutlined,
  TeamOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useAppStore } from '../store/appStore';
import { useIntl } from '../i18n';
import LivePulse from '../components/LivePulse';

const { Sider, Header, Content } = Layout;

export default function AppLayout() {
  const intl = useIntl();
  const navigate = useNavigate();
  const location = useLocation();
  const connected = useAppStore((s) => s.connected);
  const error = useAppStore((s) => s.error);
  const serverUrl = useAppStore((s) => s.serverUrl);
  const darkMode = useAppStore((s) => s.darkMode);
  const setDarkMode = useAppStore((s) => s.setDarkMode);
  const authEnabled = useAppStore((s) => s.authEnabled);
  const authUser = useAppStore((s) => s.authUser);
  const logout = useAppStore((s) => s.logout);
  const refreshAll = useAppStore((s) => s.refreshAll);
  const loading = useAppStore((s) => s.loading);
  const status = useAppStore((s) => s.status);
  const {
    token: { colorBgContainer },
  } = antdTheme.useToken();

  useEffect(() => {
    void refreshAll();
  }, [refreshAll]);

  const menuItems = useMemo(
    () => [
      { key: '/', icon: <DashboardOutlined />, label: intl.formatMessage({ id: 'layout.menu.dashboard', defaultMessage: 'Dashboard' }) },
      { key: '/projects', icon: <FolderOutlined />, label: intl.formatMessage({ id: 'layout.menu.projects', defaultMessage: 'Projects' }) },
      { key: '/artifacts', icon: <FolderOpenOutlined />, label: intl.formatMessage({ id: 'layout.menu.workspace', defaultMessage: 'Workspace' }) },
      { key: '/memory', icon: <DatabaseOutlined />, label: intl.formatMessage({ id: 'layout.menu.memory', defaultMessage: 'Memory' }) },
      { key: '/evolution', icon: <ExperimentOutlined />, label: intl.formatMessage({ id: 'layout.menu.evolution', defaultMessage: 'Evolution' }) },
      { key: '/integrations', icon: <ApiOutlined />, label: intl.formatMessage({ id: 'layout.menu.integrations', defaultMessage: 'Integrations' }) },
      { key: '/audit', icon: <AuditOutlined />, label: intl.formatMessage({ id: 'layout.menu.audit', defaultMessage: 'Audit' }) },
      { key: '/users', icon: <UserOutlined />, label: intl.formatMessage({ id: 'layout.menu.users', defaultMessage: 'Users' }) },
      { key: '/teams', icon: <TeamOutlined />, label: intl.formatMessage({ id: 'layout.menu.teams', defaultMessage: 'Teams' }) },
      { key: '/tenants', icon: <ApartmentOutlined />, label: intl.formatMessage({ id: 'layout.menu.tenants', defaultMessage: 'Tenants' }) },
      { key: '/calendar', icon: <CalendarOutlined />, label: intl.formatMessage({ id: 'layout.menu.calendar', defaultMessage: 'Calendar' }) },
      { key: '/dependencies', icon: <ShareAltOutlined />, label: intl.formatMessage({ id: 'layout.menu.dependencies', defaultMessage: 'Dependencies' }) },
      { key: '/plans', icon: <ScheduleOutlined />, label: intl.formatMessage({ id: 'layout.menu.plans', defaultMessage: 'Plans' }) },
      { key: '/settings', icon: <SettingOutlined />, label: intl.formatMessage({ id: 'layout.menu.settings', defaultMessage: 'Settings' }) },
    ],
    [intl],
  );

  const selectedKey =
    menuItems.map((m) => m.key).find((key) =>
      key === '/' ? location.pathname === '/' : location.pathname.startsWith(key),
    ) ?? '/';

  const connectionText =
    connected === false
      ? intl.formatMessage({ id: 'layout.disconnected', defaultMessage: 'Disconnected' })
      : connected
        ? intl.formatMessage({ id: 'layout.connected', defaultMessage: 'Connected' })
        : intl.formatMessage({ id: 'layout.connecting', defaultMessage: 'Connecting…' });

  return (
    <Layout style={{ minHeight: '100vh' }} data-testid="app-layout">
      <Sider breakpoint="lg" collapsedWidth={64} theme={darkMode ? 'dark' : 'light'}>
        <div style={{ padding: 16, textAlign: 'center' }}>
          <Space align="center" size={8}>
            <img src="/logo.svg" width={28} height={28} style={{ borderRadius: 6 }} alt="logo" />
            <Typography.Title level={5} style={{ margin: 0, whiteSpace: 'nowrap' }}>
              DoAI Workbench
            </Typography.Title>
          </Space>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selectedKey]}
          items={menuItems}
          onClick={(e) => navigate(e.key)}
          aria-label={intl.formatMessage({ id: 'layout.navAria', defaultMessage: 'Main navigation' })}
          data-testid="main-menu"
        />
      </Sider>
      <Layout>
        <Header
          style={{
            background: colorBgContainer,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            paddingInline: 24,
          }}
          data-testid="app-header"
        >
          <Space size="middle">
            <LivePulse connected={connected} activeSessions={status?.active_sessions} />
            <Badge
              status={connected === false ? 'error' : connected ? 'success' : 'default'}
              text={connectionText}
              data-testid="connection-status"
            />
            <Tooltip title={serverUrl}>
              <Typography.Text className="mono" type="secondary" data-testid="server-url">
                {serverUrl}
              </Typography.Text>
            </Tooltip>
          </Space>
          <Space size="middle">
            {authEnabled && authUser && (
              <Space size={8} data-testid="auth-user-space">
                <Typography.Text data-testid="auth-user">{authUser}</Typography.Text>
                <Button size="small" onClick={logout} data-testid="logout-btn">
                  {intl.formatMessage({ id: 'layout.logout', defaultMessage: 'Logout' })}
                </Button>
              </Space>
            )}
            <Button
              icon={<ReloadOutlined />}
              onClick={() => void refreshAll()}
              loading={loading}
              data-testid="refresh-all"
            >
              {intl.formatMessage({ id: 'common.refresh', defaultMessage: 'Refresh' })}
            </Button>
            <Tooltip
              title={
                darkMode
                  ? intl.formatMessage({ id: 'layout.switchToLight', defaultMessage: 'Switch to light mode' })
                  : intl.formatMessage({ id: 'layout.switchToDark', defaultMessage: 'Switch to dark mode' })
              }
            >
              <Switch
                checked={darkMode}
                onChange={setDarkMode}
                size="small"
                aria-label={intl.formatMessage({ id: 'common.darkMode', defaultMessage: 'Dark mode' })}
                data-testid="dark-mode-switch"
              />
            </Tooltip>
            <span aria-hidden="true">{darkMode ? <MoonOutlined /> : <SunOutlined />}</span>
          </Space>
        </Header>
        {connected === false && (
          <Alert
            type="error"
            showIcon
            message={intl.formatMessage({ id: 'layout.connectionFailed', defaultMessage: 'Connection failed' })}
            description={
              <Space>
                <span>
                  {error ??
                    intl.formatMessage({
                      id: 'layout.cannotReach',
                      defaultMessage: 'Cannot reach the backend service',
                    })}
                </span>
                <Button size="small" onClick={() => navigate('/settings')}>
                  {intl.formatMessage({ id: 'layout.goToSettings', defaultMessage: 'Go to settings' })}
                </Button>
              </Space>
            }
            closable
            data-testid="connection-banner"
          />
        )}
        <Content
          style={{ padding: 24 }}
          role="main"
          aria-label={intl.formatMessage({ id: 'layout.pageAria', defaultMessage: 'Page content' })}
          data-testid="page-content"
        >
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
