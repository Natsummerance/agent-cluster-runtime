import type { ReactElement } from 'react';
import { render } from '@testing-library/react';
import type { RenderOptions } from '@testing-library/react';
import { I18nProvider } from '../i18n';
import type { Locale } from '../i18n';

export function renderWithIntl(
  ui: ReactElement,
  options?: Omit<RenderOptions, 'wrapper'>,
  locale: Locale = 'zh-CN',
) {
  return render(ui, {
    ...options,
    wrapper: ({ children }) => <I18nProvider locale={locale}>{children}</I18nProvider>,
  });
}
