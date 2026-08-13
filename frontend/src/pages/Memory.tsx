import { useCallback, useEffect, useState } from 'react';
import { Button, Card, Empty, List, message, Space, Tag, Typography } from 'antd';
import { ArrowUpOutlined, ReloadOutlined } from '@ant-design/icons';
import * as api from '../api/endpoints';
import { apiErrorMessage } from '../store/appStore';
import { useProjectParam } from '../hooks/useProjectParam';
import { useIntl } from '../i18n';
import PageHeader from '../components/PageHeader';
import ProjectSelector from '../components/ProjectSelector';
import type { MemoryData, MemoryItem } from '../api/types';

export default function Memory() {
  const intl = useIntl();
  const [projectId, setProjectId] = useProjectParam();
  const [data, setData] = useState<MemoryData | null>(null);
  const [loading, setLoading] = useState(false);
  const [promoting, setPromoting] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      const result = await api.fetchMemory(projectId);
      setData(result);
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    if (projectId) void load();
    else setData(null);
  }, [projectId, load]);

  const promote = useCallback(
    async (id: string) => {
      setPromoting(id);
      try {
        await api.promoteMemory(id);
        message.success(intl.formatMessage({ id: 'memory.promoted', defaultMessage: 'Proposal promoted to long-term memory' }));
        void load();
      } catch (err) {
        message.error(apiErrorMessage(err));
      } finally {
        setPromoting(null);
      }
    },
    [load, intl],
  );

  const renderItem = (item: MemoryItem, action?: React.ReactNode) => (
    <List.Item
      key={item.id}
      actions={action ? [action] : undefined}
      data-testid={`memory-item-${item.id}`}
    >
      <List.Item.Meta
        title={
          <Space wrap>
            <Typography.Text className="mono">{item.id.slice(0, 12)}</Typography.Text>
            {(item.tags ?? []).map((tag) => (
              <Tag key={tag}>{tag}</Tag>
            ))}
            {item.created_at && (
              <Typography.Text type="secondary">{String(item.created_at).slice(0, 19)}</Typography.Text>
            )}
          </Space>
        }
        description={item.content}
      />
    </List.Item>
  );

  return (
    <div data-testid="memory-page">
      <PageHeader
        title={intl.formatMessage({ id: 'memory.header.title', defaultMessage: 'Memory store' })}
        description={intl.formatMessage({
          id: 'memory.header.desc',
          defaultMessage: 'Long-term memory and pending proposals',
        })}
      />
      <div style={{ marginBottom: 16 }}>
        <ProjectSelector value={projectId || undefined} onChange={setProjectId} />
      </div>
      {!projectId ? (
        <Empty
          description={intl.formatMessage({ id: 'memory.selectProject', defaultMessage: 'Select a project first' })}
        />
      ) : (
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Card
            title={intl.formatMessage({ id: 'memory.longTerm', defaultMessage: 'Long-term memory' })}
            extra={
              <Button size="small" icon={<ReloadOutlined />} onClick={() => void load()}>
                {intl.formatMessage({ id: 'common.refresh', defaultMessage: 'Refresh' })}
              </Button>
            }
            loading={loading}
            data-testid="memory-card"
          >
            <List
              dataSource={data?.items ?? []}
              locale={{
                emptyText: intl.formatMessage({ id: 'memory.noMemory', defaultMessage: 'No long-term memory' }),
              }}
              renderItem={(item) => renderItem(item)}
            />
          </Card>
          <Card
            title={intl.formatMessage({ id: 'memory.proposals', defaultMessage: 'Memory proposals' })}
            data-testid="proposals-card"
          >
            <List
              dataSource={data?.proposals ?? []}
              locale={{
                emptyText: intl.formatMessage({ id: 'memory.noProposals', defaultMessage: 'No proposals' }),
              }}
              renderItem={(item) =>
                renderItem(item, (
                  <Button
                    size="small"
                    type="primary"
                    icon={<ArrowUpOutlined />}
                    loading={promoting === item.id}
                    onClick={() => void promote(item.id)}
                    data-testid={`promote-${item.id}`}
                  >
                    {intl.formatMessage({ id: 'memory.promote', defaultMessage: 'Promote to memory' })}
                  </Button>
                ))
              }
            />
          </Card>
        </Space>
      )}
    </div>
  );
}
