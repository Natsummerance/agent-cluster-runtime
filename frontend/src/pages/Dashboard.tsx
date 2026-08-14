import { useEffect } from 'react';
import { Alert, Button, Card, Col, Row, Space, Statistic, Table, Tag, theme as antdTheme, Typography } from 'antd';
import {
  ApiOutlined,
  ClockCircleOutlined,
  ExperimentOutlined,
  FolderOutlined,
  RocketOutlined,
} from '@ant-design/icons';
import { Link } from 'react-router-dom';
import { useAppStore } from '../store/appStore';
import { useProjectStore } from '../store/projectStore';
import { useIntl } from '../i18n';
import PageHeader from '../components/PageHeader';
import LivePulse from '../components/LivePulse';
import type { AxisStatus, Project } from '../api/types';

function formatUptime(intl: ReturnType<typeof useIntl>, seconds: number): string {
  if (!Number.isFinite(seconds)) return '-';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return intl.formatMessage({ id: 'dashboard.hours', defaultMessage: '{h} hours {m} min' }, { h, m });
  return intl.formatMessage({ id: 'dashboard.minutes', defaultMessage: '{m} minutes' }, { m });
}

const AXIS_STATUS_ID: Record<AxisStatus, string> = {
  ok: 'dashboard.axis.ok',
  warn: 'dashboard.axis.warn',
  critical: 'dashboard.axis.critical',
};

const AXIS_STATUS_COLOR: Record<AxisStatus, string> = {
  ok: 'green',
  warn: 'orange',
  critical: 'red',
};

export default function Dashboard() {
  const intl = useIntl();
  const status = useAppStore((s) => s.status);
  const metrics = useAppStore((s) => s.metrics);
  const connected = useAppStore((s) => s.connected);
  const error = useAppStore((s) => s.error);
  const projects = useAppStore((s) => s.projects);
  const refreshProjects = useAppStore((s) => s.refreshProjects);
  const dashboardMap = useProjectStore((s) => s.dashboard);
  const loadDashboard = useProjectStore((s) => s.loadDashboard);
  const {
    token: { colorSuccess },
  } = antdTheme.useToken();

  useEffect(() => {
    void refreshProjects();
  }, [refreshProjects]);

  useEffect(() => {
    for (const project of projects) {
      if (!dashboardMap[project.id]) void loadDashboard(project.id);
    }
  }, [projects, dashboardMap, loadDashboard]);

  const axisTag = (axisStatus?: AxisStatus) => {
    if (!axisStatus)
      return <Tag>{intl.formatMessage({ id: 'dashboard.axis.noData', defaultMessage: 'No data' })}</Tag>;
    return (
      <Tag color={AXIS_STATUS_COLOR[axisStatus]}>
        {intl.formatMessage({ id: AXIS_STATUS_ID[axisStatus], defaultMessage: axisStatus })}
      </Tag>
    );
  };

  return (
    <div data-testid="dashboard">
      <PageHeader
        title={intl.formatMessage({ id: 'dashboard.header.title', defaultMessage: 'Dashboard' })}
        description={intl.formatMessage({
          id: 'dashboard.header.desc',
          defaultMessage: 'DoAI Workbench overview: status, metrics and quick links',
        })}
      />
      {connected === false && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message={intl.formatMessage({
            id: 'dashboard.connAlert.message',
            defaultMessage: 'Connection failed; please start the DoAI Workbench backend (agent-cluster serve)',
          })}
          description={
            <span>
              {error ??
                intl.formatMessage({
                  id: 'dashboard.connAlert.noError',
                  defaultMessage: 'No backend service detected',
                })}{' '}
              —{' '}
              <Link to="/settings">
                {intl.formatMessage({
                  id: 'dashboard.connAlert.hint',
                  defaultMessage: 'Go to Settings to change the server address and auth token',
                })}
              </Link>
            </span>
          }
          data-testid="dashboard-connection-alert"
        />
      )}
      <Row gutter={[16, 16]}>
        <Col xs={24} md={12} xl={8}>
          <Card
            title={intl.formatMessage({ id: 'dashboard.clusterPulse', defaultMessage: 'Cluster pulse' })}
            extra={<LivePulse connected={connected} activeSessions={status?.active_sessions} />}
            data-testid="status-card"
          >
            {status ? (
              <Row gutter={[8, 8]}>
                <Col span={12}>
                  <Statistic
                    title={intl.formatMessage({ id: 'dashboard.version', defaultMessage: 'Version' })}
                    value={status.version}
                    prefix={<RocketOutlined />}
                  />
                </Col>
                <Col span={12}>
                  <Statistic
                    title={intl.formatMessage({ id: 'dashboard.uptime', defaultMessage: 'Uptime' })}
                    value={formatUptime(intl, status.uptime)}
                    prefix={<ClockCircleOutlined />}
                  />
                </Col>
                <Col span={8}>
                  <Statistic
                    title={intl.formatMessage({ id: 'common.project', defaultMessage: 'Projects' })}
                    value={status.projects}
                    prefix={<FolderOutlined />}
                  />
                </Col>
                <Col span={8}>
                  <Statistic
                    title={intl.formatMessage({ id: 'common.session', defaultMessage: 'Sessions' })}
                    value={status.sessions}
                    prefix={<ApiOutlined />}
                  />
                </Col>
                <Col span={8}>
                  <Statistic
                    title={intl.formatMessage({ id: 'dashboard.activeSessions', defaultMessage: 'Active sessions' })}
                    value={status.active_sessions}
                    valueStyle={{ color: status.active_sessions > 0 ? colorSuccess : undefined }}
                  />
                </Col>
              </Row>
            ) : (
              <Typography.Text type="secondary">
                {intl.formatMessage({ id: 'dashboard.noStatus', defaultMessage: 'No status data' })}
              </Typography.Text>
            )}
          </Card>
        </Col>
        <Col xs={24} md={12} xl={8}>
          <Card
            title={intl.formatMessage({ id: 'dashboard.metricsCard', defaultMessage: 'Runtime metrics' })}
            data-testid="metrics-card"
          >
            {metrics ? (
              <Row gutter={[8, 8]}>
                <Col span={8}>
                  <Statistic
                    title={intl.formatMessage({ id: 'dashboard.totalSessions', defaultMessage: 'Total sessions' })}
                    value={metrics.sessions}
                  />
                </Col>
                <Col span={8}>
                  <Statistic
                    title={intl.formatMessage({ id: 'dashboard.active', defaultMessage: 'Active' })}
                    value={metrics.active}
                  />
                </Col>
                <Col span={8}>
                  <Statistic
                    title={intl.formatMessage({ id: 'dashboard.totalTokens', defaultMessage: 'Total tokens' })}
                    value={metrics.total_tokens}
                    valueStyle={{ fontSize: 16 }}
                  />
                </Col>
                <Col span={12}>
                  <Statistic
                    title={intl.formatMessage({ id: 'dashboard.totalCost', defaultMessage: 'Total cost (USD)' })}
                    value={metrics.total_cost}
                    precision={4}
                    valueStyle={{ fontSize: 18 }}
                  />
                </Col>
                <Col span={12}>
                  <Statistic
                    title={intl.formatMessage({ id: 'dashboard.lastUpdate', defaultMessage: 'Last update' })}
                    value={metrics.updated_at?.slice(0, 19) ?? '-'}
                  />
                </Col>
              </Row>
            ) : (
              <Typography.Text type="secondary">
                {intl.formatMessage({ id: 'dashboard.noMetrics', defaultMessage: 'No metrics data' })}
              </Typography.Text>
            )}
          </Card>
        </Col>
        <Col xs={24} md={12} xl={8}>
          <Card
            title={intl.formatMessage({ id: 'dashboard.quickLinks', defaultMessage: 'Quick links' })}
            data-testid="quick-links"
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <Link to="/projects">
                <Button block icon={<FolderOutlined />}>
                  {intl.formatMessage({ id: 'dashboard.projectManagement', defaultMessage: 'Project management' })}
                </Button>
              </Link>
              <Link to="/evolution">
                <Button block icon={<ExperimentOutlined />}>
                  {intl.formatMessage({ id: 'dashboard.evolutionProposals', defaultMessage: 'Evolution proposals' })}
                </Button>
              </Link>
              <Link to="/integrations">
                <Button block icon={<ApiOutlined />}>
                  {intl.formatMessage({ id: 'dashboard.pluginsSkillsMcp', defaultMessage: 'Plugins / Skills / MCP' })}
                </Button>
              </Link>
              {connected === false && (
                <Link to="/settings">
                  <Button block type="primary">
                    {intl.formatMessage({ id: 'dashboard.configureServer', defaultMessage: 'Configure server' })}
                  </Button>
                </Link>
              )}
            </div>
          </Card>
        </Col>
      </Row>
      <Card
        title={intl.formatMessage({ id: 'dashboard.overview.title', defaultMessage: 'Project three-axis overview' })}
        data-testid="projects-overview"
        style={{ marginTop: 16 }}
      >
        {projects.length ? (
          <Table<Project>
            rowKey="id"
            dataSource={projects}
            pagination={false}
            columns={[
              {
                title: intl.formatMessage({ id: 'common.project', defaultMessage: 'Project' }),
                dataIndex: 'name',
                key: 'name',
                render: (value: string, record: Project) => (
                  <Link to={`/projects/${record.id}/sessions`}>{value}</Link>
                ),
              },
              {
                title: intl.formatMessage({ id: 'dashboard.overview.cost', defaultMessage: 'Cost axis' }),
                key: 'cost',
                render: (_: unknown, record: Project) => {
                  const axis = dashboardMap[record.id]?.cost;
                  return (
                    <Space>
                      {axisTag(axis?.status)}
                      {axis ? (
                        <Typography.Text type="secondary">{Math.round(axis.ratio * 100)}%</Typography.Text>
                      ) : null}
                    </Space>
                  );
                },
              },
              {
                title: intl.formatMessage({ id: 'dashboard.overview.progress', defaultMessage: 'Progress axis' }),
                key: 'progress',
                render: (_: unknown, record: Project) => {
                  const axis = dashboardMap[record.id]?.progress;
                  return (
                    <Space>
                      {axisTag(axis?.status)}
                      {axis ? (
                        <Typography.Text type="secondary">{Math.round(axis.score * 100)}%</Typography.Text>
                      ) : null}
                    </Space>
                  );
                },
              },
              {
                title: intl.formatMessage({ id: 'dashboard.overview.health', defaultMessage: 'Health axis' }),
                key: 'health',
                render: (_: unknown, record: Project) => axisTag(dashboardMap[record.id]?.health.status),
              },
            ]}
          />
        ) : (
          <Typography.Text type="secondary">
            {intl.formatMessage({ id: 'dashboard.noProjects', defaultMessage: 'No projects' })}
          </Typography.Text>
        )}
      </Card>
    </div>
  );
}
