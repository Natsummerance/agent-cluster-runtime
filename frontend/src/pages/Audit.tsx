import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Card, Descriptions, Empty, Input, message, Space, Typography } from 'antd';
import { DownloadOutlined, SearchOutlined } from '@ant-design/icons';
import * as api from '../api/endpoints';
import { apiErrorMessage } from '../store/appStore';
import { useSessionParam } from '../hooks/useProjectParam';
import PageHeader from '../components/PageHeader';
import type { AuditData } from '../api/types';

export default function Audit() {
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
        message.success('审计包已下载');
      } else {
        message.success(`审计导出成功：${result.file ?? JSON.stringify(result)}`);
      }
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setExporting(false);
    }
  }, [sessionId]);

  return (
    <div data-testid="audit-page">
      <PageHeader
        title="审计"
        description="查看会话审批审计记录并导出审计包"
        actions={
          sessionId ? (
            <Button
              icon={<DownloadOutlined />}
              onClick={() => void doExport()}
              loading={exporting}
              data-testid="audit-export-btn"
            >
              导出审计包
            </Button>
          ) : undefined
        }
      />
      <Space style={{ marginBottom: 16 }} wrap>
        <div data-testid="audit-session-input">
          <Input.Search
            placeholder="输入会话 ID 后回车"
            defaultValue={sessionId}
            enterButton={<SearchOutlined />}
            onSearch={(value) => setSessionId(value.trim())}
            style={{ width: 360 }}
            aria-label="会话 ID 搜索"
          />
        </div>
      </Space>
      {!sessionId ? (
        <Empty description="输入会话 ID 查看审计记录" />
      ) : data ? (
        <Card loading={loading} data-testid="audit-card">
          <Descriptions size="small" column={1} style={{ marginBottom: 16 }}>
            <Descriptions.Item label="会话 ID">
              <Typography.Text className="mono">{data.session_id ?? sessionId}</Typography.Text>
            </Descriptions.Item>
            {data.summary && <Descriptions.Item label="概要">{data.summary}</Descriptions.Item>}
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
        <Alert type="warning" showIcon message="未找到审计数据" style={{ maxWidth: 480 }} />
      )}
    </div>
  );
}