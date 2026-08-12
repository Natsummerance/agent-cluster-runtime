import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Card, Empty, Table, Tabs, Tag } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import * as api from '../api/endpoints';
import { apiErrorMessage } from '../store/appStore';
import PageHeader from '../components/PageHeader';
import type { IntegrationNote } from '../api/types';

type TabKey = 'plugins' | 'skills' | 'mcp';
type ListData = unknown[] | IntegrationNote | null;

const TITLES: Record<TabKey, string> = {
  plugins: '插件',
  skills: '技能',
  mcp: 'MCP 服务器',
};

const LOADERS: Record<TabKey, () => Promise<unknown>> = {
  plugins: api.fetchPlugins,
  skills: api.fetchSkills,
  mcp: api.fetchMcp,
};

export default function Integrations() {
  const [activeTab, setActiveTab] = useState<TabKey>('plugins');
  const [data, setData] = useState<Record<TabKey, ListData>>({ plugins: null, skills: null, mcp: null });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    const entries = await Promise.all(
      (Object.keys(LOADERS) as TabKey[]).map(async (key) => {
        try {
          const value = await LOADERS[key]();
          return [key, value ?? []] as const;
        } catch (err) {
          setError(apiErrorMessage(err));
          return [key, null] as const;
        }
      }),
    );
    setData(Object.fromEntries(entries) as Record<TabKey, ListData>);
    setLoading(false);
  }, []);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  const renderTab = (key: TabKey) => {
    const value = data[key];
    if (value === null) return <Empty description="加载失败或后端未提供该数据" />;
    if (Array.isArray(value)) {
      if (value.length === 0) return <Empty description={`暂无${TITLES[key]}`} />;
      const columns = [
        { title: '名称', dataIndex: 'name', key: 'name', render: (v: string) => v ?? '-' },
        { title: '描述', dataIndex: 'description', key: 'description', render: (v?: string) => v ?? '-' },
      ];
      return (
        <Table
          rowKey={(record) => String((record as Record<string, unknown>).name ?? JSON.stringify(record))}
          columns={columns}
          dataSource={value as unknown[]}
          pagination={false}
          data-testid={`${key}-table`}
        />
      );
    }
    return (
      <Alert
        type="info"
        showIcon
        message={`${TITLES[key]}（占位）`}
        description={value.note ?? JSON.stringify(value)}
        data-testid={`${key}-note`}
      />
    );
  };

  return (
    <div data-testid="integrations-page">
      <PageHeader
        title="集成"
        description="插件、技能与 MCP 服务器清单"
        actions={
          <Button icon={<ReloadOutlined />} onClick={() => void loadAll()} loading={loading} data-testid="refresh-integrations">
            刷新
          </Button>
        }
      />
      {error && (
        <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />
      )}
      <Card>
        <Tabs
          activeKey={activeTab}
          onChange={(key) => setActiveTab(key as TabKey)}
          items={(Object.keys(TITLES) as TabKey[]).map((key) => ({
            key,
            label: (
              <span data-testid={`tab-${key}`}>
                {TITLES[key]}{' '}
                {Array.isArray(data[key]) && data[key]!.length > 0 && <Tag>{data[key]!.length}</Tag>}
              </span>
            ),
            children: renderTab(key),
          }))}
          data-testid="integrations-tabs"
        />
      </Card>
    </div>
  );
}