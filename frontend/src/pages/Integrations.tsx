import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Card, Empty, Table, Tabs, Tag } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import * as api from '../api/endpoints';
import { apiErrorMessage } from '../store/appStore';
import { useIntl } from '../i18n';
import PageHeader from '../components/PageHeader';
import type { IntegrationNote } from '../api/types';

type TabKey = 'plugins' | 'skills' | 'mcp';
type ListData = unknown[] | IntegrationNote | null;

const TAB_TITLE_ID: Record<TabKey, string> = {
  plugins: 'integrations.tab.plugins',
  skills: 'integrations.tab.skills',
  mcp: 'integrations.tab.mcp',
};

const LOADERS: Record<TabKey, () => Promise<unknown>> = {
  plugins: api.fetchPlugins,
  skills: api.fetchSkills,
  mcp: api.fetchMcp,
};

export default function Integrations() {
  const intl = useIntl();
  const [activeTab, setActiveTab] = useState<TabKey>('plugins');
  const [data, setData] = useState<Record<TabKey, ListData>>({ plugins: null, skills: null, mcp: null });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const tabTitle = (key: TabKey) =>
    intl.formatMessage({ id: TAB_TITLE_ID[key], defaultMessage: key });

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
    if (value === null)
      return (
        <Empty
          description={intl.formatMessage({
            id: 'integrations.loadFailed',
            defaultMessage: 'Load failed or the backend does not provide this data',
          })}
        />
      );
    if (Array.isArray(value)) {
      if (value.length === 0)
        return (
          <Empty
            description={intl.formatMessage(
              { id: 'integrations.emptyList', defaultMessage: 'No {title}' },
              { title: tabTitle(key) },
            )}
          />
        );
      const columns = [
        {
          title: intl.formatMessage({ id: 'common.name', defaultMessage: 'Name' }),
          dataIndex: 'name',
          key: 'name',
          render: (v: string) => v ?? '-',
        },
        {
          title: intl.formatMessage({ id: 'common.description', defaultMessage: 'Description' }),
          dataIndex: 'description',
          key: 'description',
          render: (v?: string) => v ?? '-',
        },
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
        message={intl.formatMessage(
          { id: 'integrations.placeholder', defaultMessage: '{title} (placeholder)' },
          { title: tabTitle(key) },
        )}
        description={value.note ?? JSON.stringify(value)}
        data-testid={`${key}-note`}
      />
    );
  };

  return (
    <div data-testid="integrations-page">
      <PageHeader
        title={intl.formatMessage({ id: 'integrations.header.title', defaultMessage: 'Integrations' })}
        description={intl.formatMessage({
          id: 'integrations.header.desc',
          defaultMessage: 'Plugin, skill and MCP server inventories',
        })}
        actions={
          <Button icon={<ReloadOutlined />} onClick={() => void loadAll()} loading={loading} data-testid="refresh-integrations">
            {intl.formatMessage({ id: 'common.refresh', defaultMessage: 'Refresh' })}
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
          items={(Object.keys(TAB_TITLE_ID) as TabKey[]).map((key) => ({
            key,
            label: (
              <span data-testid={`tab-${key}`}>
                {tabTitle(key)}{' '}
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
