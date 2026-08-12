import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';
import { setFetchImpl } from '../api/client';

afterEach(() => {
  cleanup();
  setFetchImpl(null);
});

// jsdom 未实现 matchMedia（antd 响应式需要）。
// 注意：不能使用 vi.fn()，否则 restoreMocks 会在每个测试后清空实现。
if (typeof window !== 'undefined' && !window.matchMedia) {
  const matchMediaMock = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  });
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: matchMediaMock,
  });
}

// jsdom 未实现 ResizeObserver（antd 部分组件需要）
if (typeof window !== 'undefined' && !window.ResizeObserver) {
  class ResizeObserverMock {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  Object.defineProperty(window, 'ResizeObserver', {
    writable: true,
    value: ResizeObserverMock,
  });
}