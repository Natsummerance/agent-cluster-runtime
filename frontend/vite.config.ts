/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: '127.0.0.1',
  },
  preview: {
    port: 5173,
  },
  build: {
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (!id.includes('node_modules')) {
            return undefined;
          }
          if (
            id.includes('@ant-design') ||
            id.includes('node_modules/antd') ||
            id.includes('rc-') ||
            id.includes('@rc-component') ||
            id.includes('dayjs') ||
            id.includes('classnames') ||
            id.includes('@babel/runtime')
          ) {
            return 'vendor-antd';
          }
          if (id.includes('react-intl') || id.includes('@formatjs') || id.includes('intl-')) {
            return 'vendor-intl';
          }
          if (id.includes('react') || id.includes('scheduler')) {
            return 'vendor-react';
          }
          return 'vendor-misc';
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/test/**/*.test.{ts,tsx}'],
    css: false,
    restoreMocks: true,
    clearMocks: true,
  },
});
