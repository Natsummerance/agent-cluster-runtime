import { useCallback, useEffect, useState } from 'react';
import { Button, Card, Empty, List, message, Space, Tag, Typography } from 'antd';
import { ArrowUpOutlined, ReloadOutlined } from '@ant-design/icons';
import * as api from '../api/endpoints';
import { apiErrorMessage } from '../store/appStore';
import { useProjectParam } from '../hooks/useProjectParam';
import PageHeader from '../components/PageHeader';
import ProjectSelector from '../components/ProjectSelector';
import type { MemoryData, MemoryItem } from '../api/types';

export default function Memory() {
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
        message.success('提案已提升为长期记忆');
        void load();
      } catch (err) {
        message.error(apiErrorMessage(err));
      } finally {
        setPromoting(null);
      }
    },
    [load],
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
      <PageHeader title="记忆库" description="长期记忆与待提升提案" />
      <div style={{ marginBottom: 16 }}>
        <ProjectSelector value={projectId || undefined} onChange={setProjectId} />
      </div>
      {!projectId ? (
        <Empty description="请先选择项目" />
      ) : (
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Card
            title="长期记忆"
            extra={
              <Button size="small" icon={<ReloadOutlined />} onClick={() => void load()}>
                刷新
              </Button>
            }
            loading={loading}
            data-testid="memory-card"
          >
            <List
              dataSource={data?.items ?? []}
              locale={{ emptyText: '暂无长期记忆' }}
              renderItem={(item) => renderItem(item)}
            />
          </Card>
          <Card title="记忆提案" data-testid="proposals-card">
            <List
              dataSource={data?.proposals ?? []}
              locale={{ emptyText: '暂无提案' }}
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
                    提升为记忆
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