// 签名元素：集群脉搏（DESIGN.md §7）。连接在线时青色呼吸光，离线红色静态。
interface LivePulseProps {
  connected: boolean | null;
  activeSessions?: number;
}

export default function LivePulse({ connected, activeSessions }: LivePulseProps) {
  const label =
    connected === false ? '集群离线' : connected ? '集群在线' : '连接中…';
  const detail =
    connected === true && typeof activeSessions === 'number' ? ` · ${activeSessions} 个活跃会话` : '';
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