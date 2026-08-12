import { Alert, Button, Card, Col, Row, Statistic, theme as antdTheme, Typography } from 'antd';
import {
  ApiOutlined,
  ClockCircleOutlined,
  ExperimentOutlined,
  FolderOutlined,
  RocketOutlined,
} from '@ant-design/icons';
import { Link } from 'react-router-dom';
import { useAppStore } from '../store/appStore';
import PageHeader from '../components/PageHeader';
import LivePulse from '../components/LivePulse';

function formatUptime(seconds: number): string {
  if (!Number.isFinite(seconds)) return '-';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h} 小时 ${m} 分`;
  return `${m} 分钟`;
}

export default function Dashboard() {
  const status = useAppStore((s) => s.status);
  const metrics = useAppStore((s) => s.metrics);
  const connected = useAppStore((s) => s.connected);
  const error = useAppStore((s) => s.error);
  const {
    token: { colorSuccess },
  } = antdTheme.useToken();

  return (
    <div data-testid="dashboard">
      <PageHeader
        title="仪表盘"
        description="Agent Cluster 集群运行总览：状态、指标与快速入口"
      />
      {connected === false && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message="连接失败，请启动 agent-cluster serve"
          description={
            <span>
              {error ?? '未检测到后端服务'} —— 可前往{' '}
              <Link to="/settings">设置</Link> 修改服务器地址与认证令牌。
            </span>
          }
          data-testid="dashboard-connection-alert"
        />
      )}
      <Row gutter={[16, 16]}>
        <Col xs={24} md={12} xl={8}>
          <Card
            title="集群脉搏"
            extra={<LivePulse connected={connected} activeSessions={status?.active_sessions} />}
            data-testid="status-card"
          >
            {status ? (
              <Row gutter={[8, 8]}>
                <Col span={12}>
                  <Statistic title="版本" value={status.version} prefix={<RocketOutlined />} />
                </Col>
                <Col span={12}>
                  <Statistic
                    title="运行时长"
                    value={formatUptime(status.uptime)}
                    prefix={<ClockCircleOutlined />}
                  />
                </Col>
                <Col span={8}>
                  <Statistic title="项目" value={status.projects} prefix={<FolderOutlined />} />
                </Col>
                <Col span={8}>
                  <Statistic title="会话" value={status.sessions} prefix={<ApiOutlined />} />
                </Col>
                <Col span={8}>
                  <Statistic
                    title="活跃会话"
                    value={status.active_sessions}
                    valueStyle={{ color: status.active_sessions > 0 ? colorSuccess : undefined }}
                  />
                </Col>
              </Row>
            ) : (
              <Typography.Text type="secondary">暂无状态数据</Typography.Text>
            )}
          </Card>
        </Col>
        <Col xs={24} md={12} xl={8}>
          <Card title="运行指标" data-testid="metrics-card">
            {metrics ? (
              <Row gutter={[8, 8]}>
                <Col span={8}>
                  <Statistic title="会话总数" value={metrics.sessions} />
                </Col>
                <Col span={8}>
                  <Statistic title="活跃" value={metrics.active} />
                </Col>
                <Col span={8}>
                  <Statistic
                    title="总 Token"
                    value={metrics.total_tokens}
                    valueStyle={{ fontSize: 16 }}
                  />
                </Col>
                <Col span={12}>
                  <Statistic
                    title="总成本（USD）"
                    value={metrics.total_cost}
                    precision={4}
                    valueStyle={{ fontSize: 18 }}
                  />
                </Col>
                <Col span={12}>
                  <Statistic title="最后更新" value={metrics.updated_at?.slice(0, 19) ?? '-'} />
                </Col>
              </Row>
            ) : (
              <Typography.Text type="secondary">暂无指标数据</Typography.Text>
            )}
          </Card>
        </Col>
        <Col xs={24} md={12} xl={8}>
          <Card title="快速入口" data-testid="quick-links">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <Link to="/projects">
                <Button block icon={<FolderOutlined />}>项目管理</Button>
              </Link>
              <Link to="/evolution">
                <Button block icon={<ExperimentOutlined />}>进化提案</Button>
              </Link>
              <Link to="/integrations">
                <Button block icon={<ApiOutlined />}>插件 / 技能 / MCP</Button>
              </Link>
              {connected === false && (
                <Link to="/settings">
                  <Button block type="primary">
                    配置服务器
                  </Button>
                </Link>
              )}
            </div>
          </Card>
        </Col>
      </Row>
    </div>
  );
}