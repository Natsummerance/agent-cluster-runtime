import { Alert, Descriptions, Progress, Tag } from 'antd';
import type { TokenInfo } from '../api/types';

function renderBreakdown(map?: Record<string, number>) {
  if (!map || Object.keys(map).length === 0) return <span>—</span>;
  return (
    <span>
      {Object.entries(map).map(([key, value]) => (
        <Tag key={key} data-testid="token-breakdown-tag">
          {key}: {value}
        </Tag>
      ))}
    </span>
  );
}

export default function TokenPanel({ token }: { token?: TokenInfo | null }) {
  if (!token) return null;
  const { budget, used, remaining, over_budget, by_phase, by_role } = token;
  const percent = budget > 0 ? Math.min(100, Math.round((used / budget) * 100)) : 0;
  const status: 'normal' | 'active' | 'exception' = over_budget
    ? 'exception'
    : percent >= 90
      ? 'active'
      : 'normal';
  return (
    <div data-testid="token-panel">
      <Progress
        percent={percent}
        status={status}
        format={() => `${used} / ${budget}`}
        data-testid="token-progress"
      />
      {over_budget && (
        <Alert
          type="error"
          showIcon
          message="Token 预算已超限，可能需要人工介入"
          style={{ marginBottom: 8 }}
        />
      )}
      <Descriptions size="small" column={1} data-testid="token-descriptions">
        <Descriptions.Item label="预算">{budget}</Descriptions.Item>
        <Descriptions.Item label="已用">{used}</Descriptions.Item>
        <Descriptions.Item label="剩余">{remaining}</Descriptions.Item>
        <Descriptions.Item label="按阶段">{renderBreakdown(by_phase)}</Descriptions.Item>
        <Descriptions.Item label="按角色">{renderBreakdown(by_role)}</Descriptions.Item>
      </Descriptions>
    </div>
  );
}