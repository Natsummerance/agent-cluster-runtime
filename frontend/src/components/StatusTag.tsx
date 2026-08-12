import { Tag } from 'antd';

const MAP: Record<string, { color: string; label: string }> = {
  running: { color: 'processing', label: '运行中' },
  waiting_approval: { color: 'warning', label: '等待审批' },
  completed: { color: 'success', label: '已完成' },
  failed: { color: 'error', label: '失败' },
};

export default function StatusTag({ status }: { status?: string | null }) {
  const key = status ?? '';
  const item = MAP[key] ?? { color: 'default', label: key || '未知' };
  return (
    <Tag color={item.color} data-testid="status-tag">
      {item.label}
    </Tag>
  );
}