import { useCallback, useEffect } from 'react';
import {
  Alert,
  Button,
  Card,
  Descriptions,
  message,
  Space,
  Spin,
  Tabs,
  Tag,
  Typography,
} from 'antd';
import { Link, useParams } from 'react-router-dom';
import { AuditOutlined } from '@ant-design/icons';
import { useSessionStore } from '../store/sessionStore';
import { apiErrorMessage } from '../store/appStore';
import PageHeader from '../components/PageHeader';
import StatusTag from '../components/StatusTag';
import TokenPanel from '../components/TokenPanel';
import GateApprovalModal from '../components/GateApprovalModal';
import InterruptInput from '../components/InterruptInput';
import ChangeHistory from '../components/ChangeHistory';
import EventTimeline from '../components/EventTimeline';

export default function SessionDetail() {
  const { sid = '' } = useParams();
  const snapshot = useSessionStore((s) => s.snapshots[sid]);
  const changes = useSessionStore((s) => s.changes[sid]);
  const loading = useSessionStore((s) => s.loading[sid] ?? false);
  const error = useSessionStore((s) => s.error);
  const approval = useSessionStore((s) => s.approval);
  const fetchSession = useSessionStore((s) => s.fetchSession);
  const fetchChanges = useSessionStore((s) => s.fetchChanges);
  const approve = useSessionStore((s) => s.approve);
  const reject = useSessionStore((s) => s.reject);
  const edit = useSessionStore((s) => s.edit);
  const respond = useSessionStore((s) => s.respond);
  const interrupt = useSessionStore((s) => s.interrupt);
  const rollback = useSessionStore((s) => s.rollback);
  const closeApproval = useSessionStore((s) => s.closeApproval);
  const clearSession = useSessionStore((s) => s.clearSession);

  useEffect(() => {
    void fetchSession(sid);
    void fetchChanges(sid);
    return () => clearSession(sid);
  }, [sid, fetchSession, fetchChanges, clearSession]);

  const runAction = useCallback(
    async (action: () => Promise<void>, successText: string) => {
      try {
        await action();
        message.success(successText);
      } catch (err) {
        message.error(apiErrorMessage(err));
      }
    },
    [],
  );

  if (!snapshot) {
    return (
      <div style={{ textAlign: 'center', padding: 48 }} data-testid="session-loading">
        <Spin tip={loading ? '加载中…' : '会话不存在或加载失败'} />
        {error && !loading && (
          <Alert
            type="error"
            showIcon
            message={error}
            action={
              <Button size="small" onClick={() => void fetchSession(sid)}>
                重试
              </Button>
            }
            style={{ marginTop: 16 }}
          />
        )}
      </div>
    );
  }

  const waitingApproval = snapshot.status === 'waiting_approval';

  const items = [
    {
      key: 'overview',
      label: '概览',
      children: (
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Card title="Token 计量" size="small">
            <TokenPanel token={snapshot.token} />
          </Card>
          {snapshot.health && (
            <Card title="健康度" size="small" data-testid="health-card">
              <Descriptions size="small" column={2}>
                <Descriptions.Item label="估算准确率">
                  {snapshot.health.estimate_accuracy ?? '-'}
                </Descriptions.Item>
                <Descriptions.Item label="返工率">{snapshot.health.rework_rate ?? '-'}</Descriptions.Item>
                <Descriptions.Item label="Token 成本">
                  {snapshot.health.token_cost ?? '-'}
                </Descriptions.Item>
                <Descriptions.Item label="评测通过趋势">
                  {Array.isArray(snapshot.health.eval_pass_rate_trend)
                    ? snapshot.health.eval_pass_rate_trend.join(' → ')
                    : '-'}
                </Descriptions.Item>
              </Descriptions>
            </Card>
          )}
          <Card title="阶段" size="small">
            {snapshot.phases && snapshot.phases.length > 0 ? (
              <Space wrap>
                {snapshot.phases.map((phase) => (
                  <Tag key={phase} color={phase === snapshot.current_phase ? 'blue' : 'default'}>
                    {phase}
                  </Tag>
                ))}
              </Space>
            ) : (
              <Typography.Text type="secondary">暂无阶段信息</Typography.Text>
            )}
          </Card>
        </Space>
      ),
    },
    {
      key: 'timeline',
      label: '时间线（SSE）',
      children: <EventTimeline sessionId={sid} />,
    },
    {
      key: 'changes',
      label: '变更历史',
      children: (
        <ChangeHistory
          data={changes}
          onRollback={(version) =>
            void runAction(() => rollback(sid, version), `已回滚到版本 ${version}`)
          }
        />
      ),
    },
  ];

  return (
    <div data-testid="session-detail">
      <PageHeader
        title="会话详情"
        description={
          <Typography.Text className="mono" type="secondary" data-testid="session-id">
            {sid}
          </Typography.Text>
        }
        actions={
          <Link to={`/audit?session_id=${sid}`}>
            <Button icon={<AuditOutlined />} data-testid="goto-audit">
              查看审计
            </Button>
          </Link>
        }
      />

      <Card className="page-card" data-testid="session-header-card">
        <Descriptions size="small" column={{ xs: 1, md: 2 }}>
          <Descriptions.Item label="目标" span={2}>
            <Typography.Text data-testid="session-goal">{snapshot.goal}</Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="状态">
            <StatusTag status={snapshot.status} />
          </Descriptions.Item>
          <Descriptions.Item label="模型">{snapshot.model ?? '-'}</Descriptions.Item>
          <Descriptions.Item label="当前阶段">{snapshot.current_phase ?? '-'}</Descriptions.Item>
          <Descriptions.Item label="当前节点">
            <Typography.Text className="mono">{snapshot.current_node ?? '-'}</Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="消息数">{snapshot.transcript_count ?? 0}</Descriptions.Item>
          <Descriptions.Item label="审批门次数">{snapshot.gate_count ?? 0}</Descriptions.Item>
        </Descriptions>
        {waitingApproval && snapshot.pending_hint && (
          <Alert
            type="warning"
            showIcon
            message="该会话正在等待人工审批"
            description={snapshot.pending_hint}
            style={{ marginTop: 12 }}
            data-testid="waiting-approval-banner"
          />
        )}
        {snapshot.error && (
          <Alert
            type="error"
            showIcon
            message={snapshot.error}
            style={{ marginTop: 12 }}
            data-testid="session-error-alert"
          />
        )}
        {snapshot.exit_code !== null && snapshot.exit_code !== undefined && (
          <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
            退出码：{snapshot.exit_code}
          </Typography.Paragraph>
        )}
      </Card>

      <Card className="page-card" title="实时打断" size="small">
        <InterruptInput
          disabled={!snapshot || snapshot.status === 'completed' || snapshot.status === 'failed'}
          onInterrupt={(text) => runAction(() => interrupt(sid, text), '打断指令已发送')}
        />
      </Card>

      <Card>
        <Tabs items={items} data-testid="session-tabs" />
      </Card>

      <GateApprovalModal
        open={approval.open && approval.sid === sid}
        hint={approval.hint}
        loading={approval.loading}
        onAccept={() => void runAction(approve, '已接受审批')}
        onReject={() => void runAction(reject, '已拒绝审批')}
        onSubmitText={(mode, text) =>
          void runAction(
            () => (mode === 'edit' ? edit(text) : respond(text)),
            mode === 'edit' ? '已提交编辑内容' : '已提交回复',
          )
        }
        onCancel={closeApproval}
      />
    </div>
  );
}