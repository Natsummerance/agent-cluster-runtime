// 全局应用 store：serverUrl / authToken / darkMode 本地持久化 + 状态/项目/指标
import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import { createIntl } from 'react-intl';
import { ApiError, configureApi, DEFAULT_BASE_URL } from '../api/client';
import * as api from '../api/endpoints';
import { DEFAULT_LOCALE, errorCodeOf, MESSAGES } from '../i18n';
import type { Locale } from '../i18n';
import type { MetricsData, Project, StatusData } from '../api/types';

export interface CreateProjectInput {
  name: string;
  workspace: string;
}

interface AppState {
  serverUrl: string;
  authToken: string;
  darkMode: boolean;
  locale: Locale;
  status: StatusData | null;
  projects: Project[];
  metrics: MetricsData | null;
  connected: boolean | null;
  error: string | null;
  loading: boolean;
  setServerUrl(url: string): void;
  setAuthToken(token: string): void;
  setDarkMode(dark: boolean): void;
  setLocale(locale: Locale): void;
  syncApi(): void;
  refreshStatus(): Promise<void>;
  refreshProjects(): Promise<void>;
  refreshMetrics(): Promise<void>;
  refreshAll(): Promise<void>;
  createProject(input: CreateProjectInput): Promise<Project>;
  resetState(): void;
}

const initialState = {
  serverUrl: DEFAULT_BASE_URL,
  authToken: '',
  darkMode: false,
  locale: DEFAULT_LOCALE,
  status: null as StatusData | null,
  projects: [] as Project[],
  metrics: null as MetricsData | null,
  connected: null as boolean | null,
  error: null as string | null,
  loading: false,
};

export const useAppStore = create<AppState>()(
  persist(
    (set, get) => ({
      ...initialState,

      setServerUrl(url: string) {
        set({ serverUrl: url });
        get().syncApi();
      },
      setAuthToken(token: string) {
        set({ authToken: token });
        get().syncApi();
      },
      setDarkMode(dark: boolean) {
        set({ darkMode: dark });
      },
      setLocale(locale: Locale) {
        set({ locale });
      },
      syncApi() {
        configureApi({ baseUrl: get().serverUrl, authToken: get().authToken || null });
      },

      async refreshStatus() {
        set({ loading: true, error: null });
        try {
          const status = await api.fetchStatus();
          set({ status, connected: true, error: null, loading: false });
        } catch (err) {
          set({ connected: false, error: apiErrorMessage(err), loading: false });
        }
      },

      async refreshProjects() {
        set({ error: null });
        try {
          const projects = await api.fetchProjects();
          set({ projects, connected: true, error: null });
        } catch (err) {
          set({ connected: false, error: apiErrorMessage(err) });
        }
      },

      async refreshMetrics() {
        set({ error: null });
        try {
          const metrics = await api.fetchMetrics();
          set({ metrics, connected: true, error: null });
        } catch (err) {
          set({ connected: false, error: apiErrorMessage(err) });
        }
      },

      async refreshAll() {
        get().syncApi();
        set({ loading: true });
        const results = await Promise.allSettled([
          get().refreshStatus(),
          get().refreshProjects(),
          get().refreshMetrics(),
        ]);
        const failed = results.some((r) => r.status === 'rejected');
        const anyConnected = results.some((r) => r.status === 'fulfilled');
        set({ loading: false });
        if (!anyConnected && !failed) {
          set({ connected: false, error: '连接失败，请确认 agent-cluster serve 已启动' });
        }
      },

      async createProject(input: CreateProjectInput): Promise<Project> {
        get().syncApi();
        try {
          const project = await api.createProject(input);
          await get().refreshProjects();
          return project;
        } catch (err) {
          set({ error: apiErrorMessage(err) });
          throw err;
        }
      },

      resetState() {
        set({ ...initialState });
      },
    }),
    {
      name: 'agent-cluster-workbench',
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        serverUrl: state.serverUrl,
        authToken: state.authToken,
        darkMode: state.darkMode,
        locale: state.locale,
      }),
      onRehydrateStorage: () => (state) => {
        if (state) {
          configureApi({ baseUrl: state.serverUrl, authToken: state.authToken || null });
        }
      },
    },
  ),
);

export function apiErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const code = errorCodeOf(err);
    if (code) {
      const locale = useAppStore.getState().locale;
      const id = `errors.${code}`;
      const messages = MESSAGES[locale];
      if (messages[id]) {
        return createIntl({ locale, messages }).formatMessage({ id });
      }
    }
    return err.message;
  }
  return err instanceof Error ? err.message : String(err);
}