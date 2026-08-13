import { Tag } from 'antd';
import { useIntl } from '../i18n';

const COLOR: Record<string, string> = {
  running: 'processing',
  waiting_approval: 'warning',
  completed: 'success',
  failed: 'error',
};

const LABEL_ID: Record<string, string> = {
  running: 'status.running',
  waiting_approval: 'status.waitingApproval',
  completed: 'status.completed',
  failed: 'status.failed',
};

export default function StatusTag({ status }: { status?: string | null }) {
  const intl = useIntl();
  const key = status ?? '';
  const labelId = LABEL_ID[key];
  const label = labelId
    ? intl.formatMessage({ id: labelId, defaultMessage: labelId })
    : key || intl.formatMessage({ id: 'status.unknown', defaultMessage: 'Unknown' });
  return (
    <Tag color={COLOR[key] ?? 'default'} data-testid="status-tag">
      {label}
    </Tag>
  );
}
