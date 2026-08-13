import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Empty, Select, Space, Timeline, Tooltip, Typography } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import type { IntlShape } from 'react-intl';
import { useSessionStore } from '../store/sessionStore';
import { useIntl } from '../i18n';
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

const TYPE_LABEL_ID: Record<string, string> = {
  session_start: 'events.type.sessionStart',
  phase_start: 'events.type.phaseStart',
  gate: 'events.type.gate',
  approval: 'events.type.approval',
  tool_call: 'events.type.toolCall',
  message: 'events.type.message',
  checkpoint: 'events.type.checkpoint',
  session_end: 'events.type.sessionEnd',
  error: 'events.type.error',
};

function typeLabel(intl: IntlShape, type?: string): string {
  if (!type) return '';
  const id = TYPE_LABEL_ID[type];
  return id ? intl.formatMessage({ id, defaultMessage: type }) : type;
}

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
  const intl = useIntl();
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
          placeholder={intl.formatMessage({ id: 'events.filterPlaceholder', defaultMessage: 'Filter by type' })}
          aria-label={intl.formatMessage({ id: 'events.filterAria', defaultMessage: 'Filter by event type' })}
          style={{ width: 200 }}
          value={filter}
          onChange={setFilter}
          options={types.map((t) => ({ value: t, label: typeLabel(intl, t) }))}
          data-testid="event-filter"
        />
        <Button
          icon={<ReloadOutlined />}
          onClick={() => subscribe(sessionId)}
          data-testid="event-reconnect"
        >
          {intl.formatMessage({ id: 'events.reconnect', defaultMessage: 'Reconnect' })}
        </Button>
        <Typography.Text type="secondary">
          {intl.formatMessage(
            { id: 'events.count', defaultMessage: '{count} events (SSE live)' },
            { count: events.length },
          )}
        </Typography.Text>
      </Space>
      {error && (
        <Alert
          type="warning"
          showIcon
          message={intl.formatMessage({ id: 'events.errorTitle', defaultMessage: 'Event stream connection error' })}
          description={error}
          style={{ marginBottom: 12 }}
        />
      )}
      {visible.length === 0 ? (
        <Empty description={intl.formatMessage({ id: 'events.empty', defaultMessage: 'No events' })} />
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
                        {typeLabel(intl, event.type)}
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
