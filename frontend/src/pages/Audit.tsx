import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Dropdown,
  Empty,
  Input,
  message,
  Modal,
  Space,
  Timeline,
  Typography,
} from 'antd';
import { DownloadOutlined, SearchOutlined } from '@ant-design/icons';
import type { MenuProps } from 'antd';
import * as api from '../api/endpoints';
import type { AuditExportFormat } from '../api/endpoints';
import { apiErrorMessage } from '../store/appStore';
import { useSessionParam } from '../hooks/useProjectParam';
import { useIntl } from '../i18n';
import PageHeader from '../components/PageHeader';
import type { AuditData, AuditExportData, AuditTrajectoryEvent } from '../api/types';

const EXPORT_FORMATS: Array<{ key: AuditExportFormat; label: string }> = [
  { key: 'csv', label: 'CSV' },
  { key: 'json', label: 'JSON' },
  { key: 'markdown', label: 'Markdown' },
];

function eventSummary(event: AuditTrajectoryEvent): string {
  const payload = event.payload;
  if (payload && typeof payload === 'object') {
    const obj = payload as Record<string, unknown>;
    const candidates = ['text', 'message', 'summary', 'hint', 'goal', 'request_id', 'node_id'];
    for (const key of candidates) {
      const value = obj[key];
      if (typeof value === 'string' && value) return value;
    }
    return JSON.stringify(payload).slice(0, 200);
  }
  if (typeof payload === 'string' && payload) return payload;
  return '';
}

function exportExtension(format?: string): string {
  if (format === 'markdown') return 'md';
  if (format === 'csv') return 'csv';
  return 'json';
}

function exportMime(format?: string): string {
  if (format === 'markdown') return 'text/markdown';
  if (format === 'csv') return 'text/csv';
  return 'application/json';
}

export default function Audit() {
  const intl = useIntl();
  const [sessionId, setSessionId] = useSessionParam();
  const [data, setData] = useState<AuditData | null>(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportResult, setExportResult] = useState<AuditExportData | null>(null);
  const [exportOpen, setExportOpen] = useState(false);

  const load = useCallback(async (sid: string) => {
    setLoading(true);
    try {
      const result = await api.fetchSessionAudit(sid);
      setData(result);
    } catch (err) {
      message.error(apiErrorMessage(err));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (sessionId) void load(sessionId);
    else setData(null);
  }, [sessionId, load]);

  const doExport = useCallback(
    async (format: AuditExportFormat) => {
      if (!sessionId) return;
      setExporting(true);
      try {
        const result = await api.exportSessionAudit(sessionId, format);
        setExportResult(result);
        setExportOpen(true);
        message.success(
          intl.formatMessage(
            { id: 'audit.exported', defaultMessage: 'Audit exported ({kind})' },
            { kind: format },
          ),
        );
      } catch (err) {
        message.error(apiErrorMessage(err));
      } finally {
        setExporting(false);
      }
    },
    [sessionId, intl],
  );

  const downloadExport = useCallback(() => {
    const result = exportResult;
    if (!result?.content || !sessionId) return;
    const blob = new Blob([result.content], { type: exportMime(result.format) });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `audit-${sessionId}.${exportExtension(result.format)}`;
    a.click();
    URL.revokeObjectURL(url);
  }, [exportResult, sessionId]);

  const exportMenu: MenuProps = {
    items: EXPORT_FORMATS.map(({ key, label }) => ({ key, label })),
    onClick: ({ key }) => void doExport(key as AuditExportFormat),
  };

  const events: AuditTrajectoryEvent[] = Array.isArray(data?.events) ? (data?.events as AuditTrajectoryEvent[]) : [];

  return (
    <div data-testid="audit-page">
      <PageHeader
        title={intl.formatMessage({ id: 'audit.header.title', defaultMessage: 'Audit' })}
        description={intl.formatMessage({
          id: 'audit.header.desc',
          defaultMessage: 'Session audit trajectory (hash-chained) with CSV / JSON / Markdown export',
        })}
        actions={
          sessionId ? (
            <Dropdown menu={exportMenu} trigger={['click']} disabled={exporting}>
              <Button
                icon={<DownloadOutlined />}
                loading={exporting}
                data-testid="audit-export-btn"
              >
                {intl.formatMessage({ id: 'audit.export', defaultMessage: 'Export audit' })}
              </Button>
            </Dropdown>
          ) : undefined
        }
      />
      <Space style={{ marginBottom: 16 }} wrap>
        <div data-testid="audit-session-input">
          <Input.Search
            placeholder={intl.formatMessage({
              id: 'audit.inputPlaceholder',
              defaultMessage: 'Enter a session ID and press Enter',
            })}
            defaultValue={sessionId}
            enterButton={<SearchOutlined />}
            onSearch={(value) => setSessionId(value.trim())}
            style={{ width: 360 }}
            aria-label={intl.formatMessage({ id: 'audit.inputAria', defaultMessage: 'Session ID search' })}
          />
        </div>
      </Space>
      {!sessionId ? (
        <Empty
          description={intl.formatMessage({
            id: 'audit.empty',
            defaultMessage: 'Enter a session ID to view audit records',
          })}
        />
      ) : data ? (
        <Card loading={loading} data-testid="audit-card">
          <Descriptions size="small" column={1} style={{ marginBottom: 16 }}>
            <Descriptions.Item label={intl.formatMessage({ id: 'audit.sessionId', defaultMessage: 'Session ID' })}>
              <Typography.Text className="mono">{data.session_id ?? sessionId}</Typography.Text>
            </Descriptions.Item>
            {data.goal ? (
              <Descriptions.Item label={intl.formatMessage({ id: 'audit.goal', defaultMessage: 'Goal' })}>
                {data.goal}
              </Descriptions.Item>
            ) : null}
            {data.summary ? (
              <Descriptions.Item label={intl.formatMessage({ id: 'audit.summary', defaultMessage: 'Summary' })}>
                {data.summary}
              </Descriptions.Item>
            ) : null}
          </Descriptions>
          <Typography.Title level={5} style={{ marginTop: 0 }}>
            {intl.formatMessage({ id: 'audit.trajectory.title', defaultMessage: 'Trajectory' })}
            <Typography.Text type="secondary" style={{ fontWeight: 'normal', marginLeft: 8 }}>
              {intl.formatMessage(
                { id: 'audit.trajectory.count', defaultMessage: '{count} events' },
                { count: events.length },
              )}
            </Typography.Text>
          </Typography.Title>
          {events.length === 0 ? (
            <Empty
              description={intl.formatMessage({
                id: 'audit.trajectory.empty',
                defaultMessage: 'No trajectory events',
              })}
            />
          ) : (
            <Timeline
              data-testid="audit-trajectory"
              items={events.map((event, idx) => {
                const seq = typeof event.seq === 'number' ? event.seq : idx;
                const summary = eventSummary(event);
                return {
                  key: `${event.seq ?? idx}-${event.type ?? ''}`,
                  children: (
                    <div data-testid={`trajectory-item-${seq}`}>
                      <Space size="small" wrap>
                        <Typography.Text strong data-testid="trajectory-type">
                          {event.type ?? 'event'}
                        </Typography.Text>
                        <Typography.Text type="secondary" className="mono">
                          #{seq}
                        </Typography.Text>
                        {event.actor ? (
                          <Typography.Text type="secondary">{event.actor}</Typography.Text>
                        ) : null}
                        {event.ts ? (
                          <Typography.Text type="secondary">{String(event.ts).slice(0, 19)}</Typography.Text>
                        ) : null}
                      </Space>
                      {summary ? (
                        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
                          {summary}
                        </Typography.Paragraph>
                      ) : null}
                    </div>
                  ),
                };
              })}
            />
          )}
        </Card>
      ) : (
        <Alert
          type="warning"
          showIcon
          message={intl.formatMessage({ id: 'audit.notFound', defaultMessage: 'No audit data found' })}
          style={{ maxWidth: 480 }}
        />
      )}
      <Modal
        open={exportOpen}
        title={intl.formatMessage({ id: 'audit.exportResult', defaultMessage: 'Audit export result' })}
        width={760}
        onCancel={() => setExportOpen(false)}
        footer={[
          <Button
            key="download"
            icon={<DownloadOutlined />}
            data-testid="audit-export-download"
            onClick={downloadExport}
          >
            {intl.formatMessage({ id: 'audit.download', defaultMessage: 'Download' })}
          </Button>,
          <Button key="close" type="primary" onClick={() => setExportOpen(false)}>
            OK
          </Button>,
        ]}
      >
        <pre
          className="code-preview"
          data-testid="audit-export-content"
          style={{ maxHeight: 400, overflow: 'auto', marginBottom: 0 }}
        >
          {exportResult?.content ?? ''}
        </pre>
      </Modal>
    </div>
  );
}
