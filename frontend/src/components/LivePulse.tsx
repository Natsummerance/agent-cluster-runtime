// 签名元素：集群脉搏（DESIGN.md §7）。连接在线时青色呼吸光，离线红色静态。
import { useIntl } from '../i18n';

interface LivePulseProps {
  connected: boolean | null;
  activeSessions?: number;
}

export default function LivePulse({ connected, activeSessions }: LivePulseProps) {
  const intl = useIntl();
  const label =
    connected === false
      ? intl.formatMessage({ id: 'livePulse.offline', defaultMessage: 'Cluster offline' })
      : connected
        ? intl.formatMessage({ id: 'livePulse.online', defaultMessage: 'Cluster online' })
        : intl.formatMessage({ id: 'livePulse.connecting', defaultMessage: 'Connecting…' });
  const detail =
    connected === true && typeof activeSessions === 'number'
      ? intl.formatMessage(
          { id: 'livePulse.activeSessions', defaultMessage: ' · {count} active sessions' },
          { count: activeSessions },
        )
      : '';
  return (
    <span className="live-pulse" role="status" aria-live="polite" data-testid="live-pulse">
      <span
        className={connected === false ? 'pulse-dot pulse-dot--down' : 'pulse-dot'}
        aria-hidden="true"
      />
      <span className="pulse-text">
        {label}
        {detail}
      </span>
    </span>
  );
}
