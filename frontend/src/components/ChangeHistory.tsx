import { Alert, Button, Empty, List, Popconfirm, Space, Tag, Typography } from 'antd';
import { RollbackOutlined } from '@ant-design/icons';
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
  const records = data?.records ?? [];
  return (
    <div data-testid="change-history">
      {data?.summary && (
        <Alert
          type="info"
          showIcon
          message="变更概要"
          description={data.summary}
          style={{ marginBottom: 12 }}
        />
      )}
      {records.length === 0 ? (
        <Empty description="暂无变更记录" />
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
                    title={`确定回滚到 ${version} 吗？`}
                    description="回滚会重置工作区至该版本"
                    onConfirm={() => onRollback(record.version ?? record.ts ?? version)}
                    okText="回滚"
                    cancelText="取消"
                  >
                    <Button size="small" icon={<RollbackOutlined />} data-testid={`rollback-${version}`}>
                      回滚
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
                      {record.ts ?? ''} {record.summary ?? '（无摘要）'}
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