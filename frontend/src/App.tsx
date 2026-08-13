import { lazy, Suspense, useEffect } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { App as AntdApp, ConfigProvider, Spin } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import enUS from 'antd/locale/en_US';
import { useAppStore } from './store/appStore';
import { I18nProvider } from './i18n';
import { buildTheme } from './theme/theme';
import ErrorBoundary from './components/ErrorBoundary';
import AppLayout from './layout/AppLayout';
import Dashboard from './pages/Dashboard';

const Projects = lazy(() => import('./pages/Projects'));
const ProjectSessions = lazy(() => import('./pages/ProjectSessions'));
const SessionDetail = lazy(() => import('./pages/SessionDetail'));
const Artifacts = lazy(() => import('./pages/Artifacts'));
const Memory = lazy(() => import('./pages/Memory'));
const Evolution = lazy(() => import('./pages/Evolution'));
const Integrations = lazy(() => import('./pages/Integrations'));
const Audit = lazy(() => import('./pages/Audit'));
const Settings = lazy(() => import('./pages/Settings'));

export default function App() {
  const darkMode = useAppStore((s) => s.darkMode);
  const locale = useAppStore((s) => s.locale);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', darkMode ? 'dark' : 'light');
  }, [darkMode]);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  return (
    <ConfigProvider locale={locale === 'en-US' ? enUS : zhCN} theme={buildTheme(darkMode)}>
      <AntdApp>
        <ErrorBoundary>
          <I18nProvider locale={locale}>
            <BrowserRouter>
              <Suspense
                fallback={
                  <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '60vh' }}>
                    <Spin size="large" />
                  </div>
                }
              >
                <Routes>
                  <Route element={<AppLayout />}>
                    <Route path="/" element={<Dashboard />} />
                    <Route path="/projects" element={<Projects />} />
                    <Route path="/projects/:pid/sessions" element={<ProjectSessions />} />
                    <Route path="/projects/:pid/sessions/:sid" element={<SessionDetail />} />
                    <Route path="/artifacts" element={<Artifacts />} />
                    <Route path="/memory" element={<Memory />} />
                    <Route path="/evolution" element={<Evolution />} />
                    <Route path="/integrations" element={<Integrations />} />
                    <Route path="/audit" element={<Audit />} />
                    <Route path="/settings" element={<Settings />} />
                    <Route path="*" element={<Navigate to="/" replace />} />
                  </Route>
                </Routes>
              </Suspense>
            </BrowserRouter>
          </I18nProvider>
        </ErrorBoundary>
      </AntdApp>
    </ConfigProvider>
  );
}
