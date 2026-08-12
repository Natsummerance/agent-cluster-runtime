import { useEffect } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { App as AntdApp, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { useAppStore } from './store/appStore';
import { buildTheme } from './theme/theme';
import ErrorBoundary from './components/ErrorBoundary';
import AppLayout from './layout/AppLayout';
import Dashboard from './pages/Dashboard';
import Projects from './pages/Projects';
import ProjectSessions from './pages/ProjectSessions';
import SessionDetail from './pages/SessionDetail';
import Artifacts from './pages/Artifacts';
import Memory from './pages/Memory';
import Evolution from './pages/Evolution';
import Integrations from './pages/Integrations';
import Audit from './pages/Audit';
import Settings from './pages/Settings';

export default function App() {
  const darkMode = useAppStore((s) => s.darkMode);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', darkMode ? 'dark' : 'light');
  }, [darkMode]);

  return (
    <ConfigProvider locale={zhCN} theme={buildTheme(darkMode)}>
      <AntdApp>
        <ErrorBoundary>
          <BrowserRouter>
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
          </BrowserRouter>
        </ErrorBoundary>
      </AntdApp>
    </ConfigProvider>
  );
}