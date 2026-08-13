import { Alert, Descriptions, Progress, Tag } from 'antd';
import { useIntl } from '../i18n';
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
  const intl = useIntl();
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
          message={intl.formatMessage({
            id: 'token.overBudget',
            defaultMessage: 'Token budget exceeded; manual intervention may be needed',
          })}
          style={{ marginBottom: 8 }}
        />
      )}
      <Descriptions size="small" column={1} data-testid="token-descriptions">
        <Descriptions.Item label={intl.formatMessage({ id: 'token.budget', defaultMessage: 'Budget' })}>
          {budget}
        </Descriptions.Item>
        <Descriptions.Item label={intl.formatMessage({ id: 'token.used', defaultMessage: 'Used' })}>
          {used}
        </Descriptions.Item>
        <Descriptions.Item label={intl.formatMessage({ id: 'token.remaining', defaultMessage: 'Remaining' })}>
          {remaining}
        </Descriptions.Item>
        <Descriptions.Item label={intl.formatMessage({ id: 'token.byPhase', defaultMessage: 'By phase' })}>
          {renderBreakdown(by_phase)}
        </Descriptions.Item>
        <Descriptions.Item label={intl.formatMessage({ id: 'token.byRole', defaultMessage: 'By role' })}>
          {renderBreakdown(by_role)}
        </Descriptions.Item>
      </Descriptions>
    </div>
  );
}
