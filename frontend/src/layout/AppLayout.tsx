import { useEffect } from 'react';
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
} from '@ant-design/icons';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useAppStore } from '../store/appStore';
import LivePulse from '../components/LivePulse';

const { Sider, Header, Content } = Layout;

const MENU_ITEMS = [
  { key: '/', icon: <DashboardOutlined />, label: '仪表盘' },
  { key: '/projects', icon: <FolderOutlined />, label: '项目' },
  { key: '/artifacts', icon: <FolderOpenOutlined />, label: '工作区' },
  { key: '/memory', icon: <DatabaseOutlined />, label: '记忆' },
  { key: '/evolution', icon: <ExperimentOutlined />, label: '进化' },
  { key: '/integrations', icon: <ApiOutlined />, label: '集成' },
  { key: '/audit', icon: <AuditOutlined />, label: '审计' },
  { key: '/settings', icon: <SettingOutlined />, label: '设置' },
];

export default function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const connected = useAppStore((s) => s.connected);
  const error = useAppStore((s) => s.error);
  const serverUrl = useAppStore((s) => s.serverUrl);
  const darkMode = useAppStore((s) => s.darkMode);
  const setDarkMode = useAppStore((s) => s.setDarkMode);
  const refreshAll = useAppStore((s) => s.refreshAll);
  const loading = useAppStore((s) => s.loading);
  const status = useAppStore((s) => s.status);
  const {
    token: { colorBgContainer },
  } = antdTheme.useToken();

  useEffect(() => {
    void refreshAll();
  }, [refreshAll]);

  const selectedKey =
    MENU_ITEMS.map((m) => m.key).find((key) =>
      key === '/' ? location.pathname === '/' : location.pathname.startsWith(key),
    ) ?? '/';

  return (
    <Layout style={{ minHeight: '100vh' }} data-testid="app-layout">
      <Sider breakpoint="lg" collapsedWidth={64} theme={darkMode ? 'dark' : 'light'}>
        <div style={{ padding: 16, textAlign: 'center' }}>
          <Typography.Title level={5} style={{ margin: 0, whiteSpace: 'nowrap' }}>
            Agent Cluster
          </Typography.Title>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selectedKey]}
          items={MENU_ITEMS}
          onClick={(e) => navigate(e.key)}
          aria-label="主导航"
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
              text={connected === false ? '未连接' : connected ? '已连接' : '连接中…'}
              data-testid="connection-status"
            />
            <Tooltip title={serverUrl}>
              <Typography.Text className="mono" type="secondary" data-testid="server-url">
                {serverUrl}
              </Typography.Text>
            </Tooltip>
          </Space>
          <Space size="middle">
            <Button
              icon={<ReloadOutlined />}
              onClick={() => void refreshAll()}
              loading={loading}
              data-testid="refresh-all"
            >
              刷新
            </Button>
            <Tooltip title={darkMode ? '切换到亮色模式' : '切换到深色模式'}>
              <Switch
                checked={darkMode}
                onChange={setDarkMode}
                size="small"
                aria-label="深色模式"
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
            message="连接失败"
            description={
              <Space>
                <span>{error ?? '无法连接到后端服务'}</span>
                <Button size="small" onClick={() => navigate('/settings')}>
                  前往设置
                </Button>
              </Space>
            }
            closable
            data-testid="connection-banner"
          />
        )}
        <Content style={{ padding: 24 }} role="main" aria-label="页面内容" data-testid="page-content">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}