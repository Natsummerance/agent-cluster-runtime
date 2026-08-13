import type { ReactNode } from 'react';
import { IntlProvider, useIntl } from 'react-intl';
import { ApiError } from '../api/client';
import enUS from './messages/en-US.json';
import zhCN from './messages/zh-CN.json';

export type Locale = 'zh-CN' | 'en-US';

export const LOCALES: Locale[] = ['zh-CN', 'en-US'];
export const DEFAULT_LOCALE: Locale = 'zh-CN';

export const MESSAGES: Record<Locale, Record<string, string>> = {
  'zh-CN': zhCN as Record<string, string>,
  'en-US': enUS as Record<string, string>,
};

export const KNOWN_ERROR_CODES = [
  'not_authorized',
  'not_found',
  'bad_request',
  'conflict',
  'budget_pool_exhausted',
  'fork_conflict',
  'session_busy',
  'bad_frame',
  'unknown_type',
] as const;

export function errorCodeOf(err: unknown): string | undefined {
  if (err instanceof ApiError && err.payload && typeof err.payload === 'object') {
    const code = (err.payload as Record<string, unknown>).code;
    return typeof code === 'string' && code ? code : undefined;
  }
  return undefined;
}

export function I18nProvider({
  locale,
  children,
}: {
  locale: Locale;
  children: ReactNode;
}) {
  return (
    <IntlProvider locale={locale} messages={MESSAGES[locale]} defaultLocale={DEFAULT_LOCALE}>
      {children}
    </IntlProvider>
  );
}

export { useIntl };
