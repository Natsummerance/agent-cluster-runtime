import { Alert, Button, Empty, List, Popconfirm, Space, Tag, Typography } from 'antd';
import { RollbackOutlined } from '@ant-design/icons';
import { useIntl } from '../i18n';
import type { ChangeData, ChangeRecord } from '../api/types';

interface ChangeHistoryProps {
  data?: ChangeData | null;
  loading?: boolean;
  onRollback: (version: string | number) => void;
}

export function formatVersion(record: ChangeRecord): string {
  return String(record.version ?? record.ts ?? '-');
}

export default function ChangeHistory({ data, loading = false, onRollback }: ChangeHistoryProps) {
  const intl = useIntl();
  const records = data?.records ?? [];
  return (
    <div data-testid="change-history">
      {data?.summary && (
        <Alert
          type="info"
          showIcon
          message={intl.formatMessage({ id: 'changeHistory.summary', defaultMessage: 'Change summary' })}
          description={data.summary}
          style={{ marginBottom: 12 }}
        />
      )}
      {records.length === 0 ? (
        <Empty description={intl.formatMessage({ id: 'changeHistory.empty', defaultMessage: 'No change records' })} />
      ) : (
        <List
          loading={loading}
          dataSource={records}
          renderItem={(record) => {
            const version = formatVersion(record);
            return (
              <List.Item
                actions={[
                  <Popconfirm
                    key="rollback"
                    title={intl.formatMessage(
                      { id: 'changeHistory.confirmTitle', defaultMessage: 'Rollback to {version}?' },
                      { version },
                    )}
                    description={intl.formatMessage({
                      id: 'changeHistory.confirmDesc',
                      defaultMessage: 'Rollback resets the workspace to this version',
                    })}
                    onConfirm={() => onRollback(record.version ?? record.ts ?? version)}
                    okText={intl.formatMessage({ id: 'changeHistory.rollback', defaultMessage: 'Rollback' })}
                    cancelText={intl.formatMessage({ id: 'common.cancel', defaultMessage: 'Cancel' })}
                  >
                    <Button size="small" icon={<RollbackOutlined />} data-testid={`rollback-${version}`}>
                      {intl.formatMessage({ id: 'changeHistory.rollback', defaultMessage: 'Rollback' })}
                    </Button>
                  </Popconfirm>,
                ]}
              >
                <List.Item.Meta
                  title={
                    <Space>
                      <Tag color="blue" data-testid="change-version">
                        v{version}
                      </Tag>
                      {record.type && <Tag>{String(record.type)}</Tag>}
                    </Space>
                  }
                  description={
                    <Typography.Text type="secondary">
                      {record.ts ?? ''}{' '}
                      {record.summary ??
                        intl.formatMessage({ id: 'changeHistory.noSummary', defaultMessage: '(No summary)' })}
                    </Typography.Text>
                  }
                />
              </List.Item>
            );
          }}
        />
      )}
    </div>
  );
}
