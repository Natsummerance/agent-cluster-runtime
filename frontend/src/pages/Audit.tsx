import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Card, Descriptions, Empty, Input, message, Space, Typography } from 'antd';
import { DownloadOutlined, SearchOutlined } from '@ant-design/icons';
import * as api from '../api/endpoints';
import { apiErrorMessage } from '../store/appStore';
import { useSessionParam } from '../hooks/useProjectParam';
import { useIntl } from '../i18n';
import PageHeader from '../components/PageHeader';
import type { AuditData } from '../api/types';

export default function Audit() {
  const intl = useIntl();
  const [sessionId, setSessionId] = useSessionParam();
  const [data, setData] = useState<AuditData | null>(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);

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

  const doExport = useCallback(async () => {
    if (!sessionId) return;
    setExporting(true);
    try {
      const result = await api.exportSessionAudit(sessionId);
      if (result.content) {
        const blob = new Blob([result.content], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `audit-${sessionId}.json`;
        a.click();
        URL.revokeObjectURL(url);
        message.success(intl.formatMessage({ id: 'audit.downloaded', defaultMessage: 'Audit bundle downloaded' }));
      } else {
        message.success(
          intl.formatMessage(
            { id: 'audit.exported', defaultMessage: 'Audit exported: {file}' },
            { file: result.file ?? JSON.stringify(result) },
          ),
        );
      }
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setExporting(false);
    }
  }, [sessionId, intl]);

  return (
    <div data-testid="audit-page">
      <PageHeader
        title={intl.formatMessage({ id: 'audit.header.title', defaultMessage: 'Audit' })}
        description={intl.formatMessage({
          id: 'audit.header.desc',
          defaultMessage: 'View session approval audit records and export the audit bundle',
        })}
        actions={
          sessionId ? (
            <Button
              icon={<DownloadOutlined />}
              onClick={() => void doExport()}
              loading={exporting}
              data-testid="audit-export-btn"
            >
              {intl.formatMessage({ id: 'audit.export', defaultMessage: 'Export audit bundle' })}
            </Button>
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
            {data.summary && (
              <Descriptions.Item label={intl.formatMessage({ id: 'audit.summary', defaultMessage: 'Summary' })}>
                {data.summary}
              </Descriptions.Item>
            )}
          </Descriptions>
          {Array.isArray(data.records) ? (
            <pre className="code-preview" data-testid="audit-records">
              {JSON.stringify(data.records, null, 2)}
            </pre>
          ) : (
            <pre className="code-preview" data-testid="audit-json">
              {JSON.stringify(data, null, 2)}
            </pre>
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
    </div>
  );
}
