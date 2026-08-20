import { lazy, Suspense, useEffect } from 'react';
import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom';
import { App as AntdApp, ConfigProvider, Spin } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import enUS from 'antd/locale/en_US';
import { useAppStore } from './store/appStore';
import { I18nProvider } from './i18n';
import { buildTheme } from './theme/theme';
import ErrorBoundary from './components/ErrorBoundary';
import AppLayout from './layout/AppLayout';
const Dashboard = lazy(() => import('./pages/Dashboard'));
const QuickRun = lazy(() => import('./pages/QuickRun'));
const Projects = lazy(() => import('./pages/Projects'));
const ProjectSessions = lazy(() => import('./pages/ProjectSessions'));
const SessionDetail = lazy(() => import('./pages/SessionDetail'));
const Artifacts = lazy(() => import('./pages/Artifacts'));
const Memory = lazy(() => import('./pages/Memory'));
const Evolution = lazy(() => import('./pages/Evolution'));
const Integrations = lazy(() => import('./pages/Integrations'));
const Audit = lazy(() => import('./pages/Audit'));
const Settings = lazy(() => import('./pages/Settings'));
const Users = lazy(() => import('./pages/Users'));
const Teams = lazy(() => import('./pages/Teams'));
const Tenants = lazy(() => import('./pages/Tenants'));
const CalendarPage = lazy(() => import('./pages/Calendar'));
const DependenciesPage = lazy(() => import('./pages/Dependencies'));
const PlansPage = lazy(() => import('./pages/Plans'));
const Login = lazy(() => import('./pages/Login'));

function RequireAuth() {
  const authEnabled = useAppStore((s) => s.authEnabled);
  const accessToken = useAppStore((s) => s.accessToken);
  const location = useLocation();
  if (authEnabled && !accessToken && location.pathname !== '/login') {
    return <Navigate to="/login" replace />;
  }
  return <Outlet />;
}

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
                  <Route path="/login" element={<Login />} />
                  <Route element={<RequireAuth />}>
                    <Route element={<AppLayout />}>
                      <Route path="/" element={<QuickRun />} />
                      <Route path="/dashboard" element={<Dashboard />} />
                      <Route path="/projects" element={<Projects />} />
                    <Route path="/projects/:pid/sessions" element={<ProjectSessions />} />
                    <Route path="/projects/:pid/sessions/:sid" element={<SessionDetail />} />
                    <Route path="/artifacts" element={<Artifacts />} />
                    <Route path="/memory" element={<Memory />} />
                    <Route path="/evolution" element={<Evolution />} />
                    <Route path="/integrations" element={<Integrations />} />
                    <Route path="/audit" element={<Audit />} />
                    <Route path="/settings" element={<Settings />} />
                    <Route path="/users" element={<Users />} />
                    <Route path="/teams" element={<Teams />} />
                    <Route path="/tenants" element={<Tenants />} />
                    <Route path="/calendar" element={<CalendarPage />} />
                    <Route path="/dependencies" element={<DependenciesPage />} />
                    <Route path="/plans" element={<PlansPage />} />
                      <Route path="*" element={<Navigate to="/" replace />} />
                    </Route>
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
