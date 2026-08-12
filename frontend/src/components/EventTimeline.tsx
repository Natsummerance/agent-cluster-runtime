import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Empty, Select, Space, Timeline, Tooltip, Typography } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { useSessionStore } from '../store/sessionStore';
import type { SessionEvent } from '../api/types';

const TYPE_COLOR: Record<string, string> = {
  session_start: 'blue',
  phase_start: 'geekblue',
  gate: 'orange',
  approval: 'gold',
  tool_call: 'purple',
  message: 'green',
  checkpoint: 'cyan',
  session_end: 'green',
  error: 'red',
};

const TYPE_LABEL: Record<string, string> = {
  session_start: '会话开始',
  phase_start: '阶段开始',
  gate: '审批门',
  approval: '审批',
  tool_call: '工具调用',
  message: '消息',
  checkpoint: '检查点',
  session_end: '会话结束',
  error: '错误',
};

function eventSummary(event: SessionEvent): string {
  const d = event.data;
  if (d && typeof d === 'object') {
    const obj = d as Record<string, unknown>;
    const candidates = ['text', 'message', 'summary', 'hint', 'phase', 'node', 'name'];
    for (const key of candidates) {
      const value = obj[key];
      if (typeof value === 'string' && value) return value;
    }
    return JSON.stringify(d).slice(0, 200);
  }
  if (typeof d === 'string' && d) return d;
  return '';
}

export default function EventTimeline({ sessionId }: { sessionId: string }) {
  const events = useSessionStore((s) => s.events[sessionId] ?? []);
  const error = useSessionStore((s) => s.eventErrors[sessionId] ?? null);
  const subscribe = useSessionStore((s) => s.subscribe);
  const [filter, setFilter] = useState<string | undefined>(undefined);

  useEffect(() => {
    const stop = subscribe(sessionId);
    return () => stop();
  }, [sessionId, subscribe]);

  const types = useMemo(() => {
    const set = new Set<string>();
    for (const e of events) if (e.type) set.add(e.type);
    return [...set].sort();
  }, [events]);

  const visible = useMemo(() => {
    if (!filter) return events;
    return events.filter((e) => e.type === filter);
  }, [events, filter]);

  return (
    <div data-testid="event-timeline" aria-live="polite">
      <Space style={{ marginBottom: 12 }} wrap>
        <Select
          allowClear
          placeholder="按类型过滤"
          aria-label="按事件类型过滤"
          style={{ width: 200 }}
          value={filter}
          onChange={setFilter}
          options={types.map((t) => ({ value: t, label: TYPE_LABEL[t] ?? t }))}
          data-testid="event-filter"
        />
        <Button
          icon={<ReloadOutlined />}
          onClick={() => subscribe(sessionId)}
          data-testid="event-reconnect"
        >
          重连/刷新
        </Button>
        <Typography.Text type="secondary">共 {events.length} 条事件（SSE 实时）</Typography.Text>
      </Space>
      {error && (
        <Alert
          type="warning"
          showIcon
          message="事件流连接异常"
          description={error}
          style={{ marginBottom: 12 }}
        />
      )}
      {visible.length === 0 ? (
        <Empty description="暂无事件" />
      ) : (
        <Timeline
          items={visible
            .slice()
            .reverse()
            .map((event, idx) => {
              const seq = typeof event.seq === 'number' ? event.seq : idx + 1;
              const color = TYPE_COLOR[event.type] ?? 'gray';
              const summary = eventSummary(event);
              return {
                color,
                key: `${event.seq ?? event.type}-${idx}`,
                children: (
                  <div data-testid={`event-item-${seq}`}>
                    <Space size="small" wrap>
                      <Typography.Text strong data-testid="event-type">
                        {TYPE_LABEL[event.type] ?? event.type}
                      </Typography.Text>
                      <Typography.Text type="secondary" className="mono">
                        #{seq}
                      </Typography.Text>
                      {event.ts && (
                        <Tooltip title={event.ts}>
                          <Typography.Text type="secondary">{String(event.ts).slice(0, 19)}</Typography.Text>
                        </Tooltip>
                      )}
                    </Space>
                    {summary && (
                      <div>
                        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
                          {summary}
                        </Typography.Paragraph>
                      </div>
                    )}
                  </div>
                ),
              };
            })}
        />
      )}
    </div>
  );
}