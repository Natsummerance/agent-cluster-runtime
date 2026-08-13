import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Input,
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
import { useIntl } from '../i18n';
import PageHeader from '../components/PageHeader';
import StatusTag from '../components/StatusTag';
import TokenPanel from '../components/TokenPanel';
import GateApprovalModal from '../components/GateApprovalModal';
import InterruptInput from '../components/InterruptInput';
import ChangeHistory from '../components/ChangeHistory';
import EventTimeline from '../components/EventTimeline';

export default function SessionDetail() {
  const intl = useIntl();
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
  const stdin = useSessionStore((s) => s.stdin);
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

  const [stdinText, setStdinText] = useState('');
  const [stdinLoading, setStdinLoading] = useState(false);
  const submitStdin = useCallback(async () => {
    const value = stdinText.trim();
    if (!value || stdinLoading) return;
    setStdinLoading(true);
    try {
      await stdin(sid, value);
      setStdinText('');
      message.success(
        intl.formatMessage({ id: 'sd.stdinInjected', defaultMessage: 'Realtime input injected' }),
      );
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setStdinLoading(false);
    }
  }, [sid, stdinText, stdinLoading, stdin, intl]);

  if (!snapshot) {
    return (
      <div style={{ textAlign: 'center', padding: 48 }} data-testid="session-loading">
        <Spin
          tip={
            loading
              ? intl.formatMessage({ id: 'sd.loading', defaultMessage: 'Loading…' })
              : intl.formatMessage({ id: 'sd.missing', defaultMessage: 'Session missing or failed to load' })
          }
        />
        {error && !loading && (
          <Alert
            type="error"
            showIcon
            message={error}
            action={
              <Button size="small" onClick={() => void fetchSession(sid)}>
                {intl.formatMessage({ id: 'common.retry', defaultMessage: 'Retry' })}
              </Button>
            }
            style={{ marginTop: 16 }}
          />
        )}
      </div>
    );
  }

  const waitingApproval = snapshot.status === 'waiting_approval';
  const stdinEnabled =
    snapshot.status === 'running' || snapshot.status === 'waiting_approval';

  const items = [
    {
      key: 'overview',
      label: intl.formatMessage({ id: 'sd.overview', defaultMessage: 'Overview' }),
      children: (
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Card
            title={intl.formatMessage({ id: 'sd.tokenMetering', defaultMessage: 'Token metering' })}
            size="small"
          >
            <TokenPanel token={snapshot.token} />
          </Card>
          {snapshot.health && (
            <Card
              title={intl.formatMessage({ id: 'sd.health', defaultMessage: 'Health' })}
              size="small"
              data-testid="health-card"
            >
              <Descriptions size="small" column={2}>
                <Descriptions.Item
                  label={intl.formatMessage({ id: 'sd.estimateAccuracy', defaultMessage: 'Estimate accuracy' })}
                >
                  {snapshot.health.estimate_accuracy ?? '-'}
                </Descriptions.Item>
                <Descriptions.Item
                  label={intl.formatMessage({ id: 'sd.reworkRate', defaultMessage: 'Rework rate' })}
                >
                  {snapshot.health.rework_rate ?? '-'}
                </Descriptions.Item>
                <Descriptions.Item
                  label={intl.formatMessage({ id: 'sd.tokenCost', defaultMessage: 'Token cost' })}
                >
                  {snapshot.health.token_cost ?? '-'}
                </Descriptions.Item>
                <Descriptions.Item
                  label={intl.formatMessage({ id: 'sd.evalPassTrend', defaultMessage: 'Eval pass trend' })}
                >
                  {Array.isArray(snapshot.health.eval_pass_rate_trend)
                    ? snapshot.health.eval_pass_rate_trend.join(' → ')
                    : '-'}
                </Descriptions.Item>
              </Descriptions>
            </Card>
          )}
          <Card
            title={intl.formatMessage({ id: 'sd.phases', defaultMessage: 'Phases' })}
            size="small"
          >
            {snapshot.phases && snapshot.phases.length > 0 ? (
              <Space wrap>
                {snapshot.phases.map((phase) => (
                  <Tag key={phase} color={phase === snapshot.current_phase ? 'blue' : 'default'}>
                    {phase}
                  </Tag>
                ))}
              </Space>
            ) : (
              <Typography.Text type="secondary">
                {intl.formatMessage({ id: 'sd.noPhases', defaultMessage: 'No phase info' })}
              </Typography.Text>
            )}
          </Card>
        </Space>
      ),
    },
    {
      key: 'timeline',
      label: intl.formatMessage({ id: 'sd.timeline', defaultMessage: 'Timeline (SSE)' }),
      children: <EventTimeline sessionId={sid} />,
    },
    {
      key: 'changes',
      label: intl.formatMessage({ id: 'sd.changes', defaultMessage: 'Change history' }),
      children: (
        <ChangeHistory
          data={changes}
          onRollback={(version) =>
            void runAction(
              () => rollback(sid, version),
              intl.formatMessage({ id: 'sd.rolledBack', defaultMessage: 'Rolled back to version {version}' }, { version }),
            )
          }
        />
      ),
    },
  ];

  return (
    <div data-testid="session-detail">
      <PageHeader
        title={intl.formatMessage({ id: 'sd.title', defaultMessage: 'Session detail' })}
        description={
          <Typography.Text className="mono" type="secondary" data-testid="session-id">
            {sid}
          </Typography.Text>
        }
        actions={
          <Link to={`/audit?session_id=${sid}`}>
            <Button icon={<AuditOutlined />} data-testid="goto-audit">
              {intl.formatMessage({ id: 'sd.viewAudit', defaultMessage: 'View audit' })}
            </Button>
          </Link>
        }
      />

      <Card className="page-card" data-testid="session-header-card">
        <Descriptions size="small" column={{ xs: 1, md: 2 }}>
          <Descriptions.Item
            label={intl.formatMessage({ id: 'common.goal', defaultMessage: 'Goal' })}
            span={2}
          >
            <Typography.Text data-testid="session-goal">{snapshot.goal}</Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label={intl.formatMessage({ id: 'common.status', defaultMessage: 'Status' })}>
            <StatusTag status={snapshot.status} />
          </Descriptions.Item>
          <Descriptions.Item label={intl.formatMessage({ id: 'common.model', defaultMessage: 'Model' })}>
            {snapshot.model ?? '-'}
          </Descriptions.Item>
          <Descriptions.Item label={intl.formatMessage({ id: 'sd.currentPhase', defaultMessage: 'Current phase' })}>
            {snapshot.current_phase ?? '-'}
          </Descriptions.Item>
          <Descriptions.Item label={intl.formatMessage({ id: 'sd.currentNode', defaultMessage: 'Current node' })}>
            <Typography.Text className="mono">{snapshot.current_node ?? '-'}</Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label={intl.formatMessage({ id: 'sd.messageCount', defaultMessage: 'Messages' })}>
            {snapshot.transcript_count ?? 0}
          </Descriptions.Item>
          <Descriptions.Item label={intl.formatMessage({ id: 'sd.gateCount', defaultMessage: 'Gate approvals' })}>
            {snapshot.gate_count ?? 0}
          </Descriptions.Item>
        </Descriptions>
        {waitingApproval && snapshot.pending_hint && (
          <Alert
            type="warning"
            showIcon
            message={intl.formatMessage({ id: 'sd.waitingApproval', defaultMessage: 'This session is waiting for human approval' })}
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
            {intl.formatMessage({ id: 'sd.exitCode', defaultMessage: 'Exit code: {code}' }, { code: snapshot.exit_code })}
          </Typography.Paragraph>
        )}
      </Card>

      <Card
        className="page-card"
        title={intl.formatMessage({ id: 'sd.realtimeInput', defaultMessage: 'Realtime input' })}
        size="small"
        data-testid="stdin-card"
      >
        <Typography.Text type="secondary">
          {intl.formatMessage({ id: 'sd.stdinHint', defaultMessage: 'Inject realtime input into the running session: answered as the current question when suspended, otherwise injected at the next node boundary.' })}
        </Typography.Text>
        <Space.Compact style={{ width: '100%', marginTop: 8 }}>
          <Input
            value={stdinText}
            onChange={(e) => setStdinText(e.target.value)}
            onPressEnter={() => void submitStdin()}
            placeholder={intl.formatMessage({ id: 'sd.stdinPlaceholder', defaultMessage: 'e.g. Continue / additional notes…' })}
            disabled={!stdinEnabled}
            data-testid="stdin-text"
          />
          <Button
            type="primary"
            onClick={() => void submitStdin()}
            loading={stdinLoading}
            disabled={!stdinEnabled || !stdinText.trim()}
            data-testid="stdin-submit"
          >
            {intl.formatMessage({ id: 'sd.inject', defaultMessage: 'Inject' })}
          </Button>
        </Space.Compact>
      </Card>

      <Card
        className="page-card"
        title={intl.formatMessage({ id: 'sd.realtimeInterrupt', defaultMessage: 'Realtime interrupt' })}
        size="small"
      >
        <InterruptInput
          disabled={!snapshot || snapshot.status === 'completed' || snapshot.status === 'failed'}
          onInterrupt={(text) =>
            runAction(
              () => interrupt(sid, text),
              intl.formatMessage({ id: 'sd.interruptSent', defaultMessage: 'Interrupt command sent' }),
            )
          }
        />
      </Card>

      <Card>
        <Tabs items={items} data-testid="session-tabs" />
      </Card>

      <GateApprovalModal
        open={approval.open && approval.sid === sid}
        hint={approval.hint}
        loading={approval.loading}
        onAccept={() =>
          void runAction(
            approve,
            intl.formatMessage({ id: 'sd.approvalAccepted', defaultMessage: 'Approval accepted' }),
          )
        }
        onReject={() =>
          void runAction(
            reject,
            intl.formatMessage({ id: 'sd.approvalRejected', defaultMessage: 'Approval rejected' }),
          )
        }
        onSubmitText={(mode, text) =>
          void runAction(
            () => (mode === 'edit' ? edit(text) : respond(text)),
            mode === 'edit'
              ? intl.formatMessage({ id: 'sd.editSubmitted', defaultMessage: 'Edit submitted' })
              : intl.formatMessage({ id: 'sd.replySubmitted', defaultMessage: 'Reply submitted' }),
          )
        }
        onCancel={closeApproval}
      />
    </div>
  );
}
