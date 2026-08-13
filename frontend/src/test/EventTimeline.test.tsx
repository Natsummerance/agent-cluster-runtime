import { beforeEach, describe, expect, it } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { renderWithIntl } from './renderWithIntl';
import EventTimeline from '../components/EventTimeline';
import { useSessionStore } from '../store/sessionStore';
import { configureApi, setFetchImpl } from '../api/client';

function sseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    }),
    { status: 200 },
  );
}

beforeEach(() => {
  configureApi({ baseUrl: 'http://127.0.0.1:8765', authToken: null });
  useSessionStore.getState().resetState();
});

describe('EventTimeline', () => {
  it('通过 SSE 渲染事件时间线', async () => {
    setFetchImpl(async () =>
      sseResponse([
        'data: {"seq":1,"type":"session_start","ts":"2026-01-01T00:00:00Z","data":{"text":"start"}}\n\n',
        'data: {"seq":2,"type":"phase_start","ts":"2026-01-01T00:01:00Z","data":{"phase":"需求评审"}}\n\n',
      ]),
    );
    renderWithIntl(<EventTimeline sessionId="s1" />);
    // 等待最终稳定渲染（两条事件同时出现），避免捕获过渡 DOM
    await waitFor(() => {
      expect(screen.queryByTestId('event-item-2')).toBeInTheDocument();
    });
    expect(screen.getByText('会话开始')).toBeInTheDocument();
    expect(screen.getByText('阶段开始')).toBeInTheDocument();
    expect(screen.getByTestId('event-item-1')).toBeInTheDocument();
  });

  it('无事件时显示空状态', async () => {
    setFetchImpl(async () => sseResponse([]));
    renderWithIntl(<EventTimeline sessionId="s2" />);
    expect(await screen.findByText('暂无事件')).toBeInTheDocument();
  });

  it('事件流异常时显示告警', async () => {
    setFetchImpl(async () => {
      throw new TypeError('Failed to fetch');
    });
    renderWithIntl(<EventTimeline sessionId="s3" />);
    expect(await screen.findByTestId('event-timeline')).toBeInTheDocument();
    expect(await screen.findByText(/事件流连接异常/)).toBeInTheDocument();
  });
});