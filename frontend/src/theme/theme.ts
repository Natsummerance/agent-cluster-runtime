// 设计 token：所有组件经由 AntD ConfigProvider 或 CSS 变量消费，禁止散装原始常量。
// 语义基准见 frontend/DESIGN.md。
import { theme as antdTheme, type ThemeConfig } from 'antd';

export const brandToken = {
  colorPrimary: '#0f8f8f',
  colorInfo: '#0f8f8f',
  colorLink: '#0f8f8f',
  colorLinkHover: '#12a8a8',
  colorSuccess: '#2e9e5b',
  borderRadius: 8,
  borderRadiusSM: 6,
  borderRadiusLG: 12,
  fontFamily:
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif",
  fontFamilyCode: "'SFMono-Regular', Consolas, 'Cascadia Code', 'Liberation Mono', Menlo, monospace",
  sizeStep: 4,
  controlHeight: 34,
} as const;

export function buildTheme(dark: boolean): ThemeConfig {
  return {
    algorithm: dark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
    token: {
      ...brandToken,
      colorBgLayout: dark ? '#0f1416' : '#f4f7f8',
      colorBgContainer: dark ? '#14181c' : '#ffffff',
      colorBorderSecondary: dark ? '#2a3138' : '#e6ebef',
      colorText: dark ? '#e6edf3' : '#1f2933',
      colorTextSecondary: dark ? '#8b98a5' : '#5f6b7a',
    },
    components: {
      Layout: {
        headerBg: dark ? '#14181c' : '#ffffff',
        siderBg: dark ? '#101418' : '#fbfcfd',
        headerHeight: 56,
      },
      Card: {
        borderRadiusLG: 12,
      },
      Table: {
        headerBg: dark ? '#161b20' : '#f4f7f8',
      },
    },
  };
}