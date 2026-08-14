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
  accessToken: string;
  refreshToken: string;
  authUser: string | null;
  authEnabled: boolean;
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
  setTokens(accessToken: string, refreshToken: string): void;
  login(username: string, password: string): Promise<string>;
  logout(): void;
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
  accessToken: '',
  refreshToken: '',
  authUser: null as string | null,
  authEnabled: false,
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
      setTokens(accessToken: string, refreshToken: string) {
        set({ accessToken, refreshToken, authEnabled: true });
        configureApi({ baseUrl: get().serverUrl, authToken: `Bearer ${accessToken}` });
      },
      async login(username: string, password: string) {
        get().syncApi();
        try {
          const result = await api.login({ username, password });
          set({
            accessToken: result.access_token,
            refreshToken: result.refresh_token,
            authUser: result.user,
            authEnabled: true,
          });
          configureApi({ baseUrl: get().serverUrl, authToken: `Bearer ${result.access_token}` });
          return result.user;
        } catch (err) {
          set({ error: apiErrorMessage(err) });
          throw err;
        }
      },
      logout() {
        set({ accessToken: '', refreshToken: '', authUser: null, authEnabled: false });
        get().syncApi();
      },
      setDarkMode(dark: boolean) {
        set({ darkMode: dark });
      },
      setLocale(locale: Locale) {
        set({ locale });
      },
      syncApi() {
        configureApi({
          baseUrl: get().serverUrl,
          authToken: get().accessToken ? `Bearer ${get().accessToken}` : get().authToken || null,
        });
      },

      async refreshStatus() {
        set({ loading: true, error: null });
        try {
          const status = await api.fetchStatus();
          set({ status, connected: true, error: null, loading: false });
          if (status?.auth) {
            set({
              authEnabled: Boolean(status.auth.enabled),
              authUser: status.auth.enabled ? (status.auth.user ?? get().authUser) : null,
            });
          }
        } catch (err) {
          if (err instanceof ApiError && err.status === 401) {
            // 401 = 服务端已启用认证：切到登录页（无需 token 即可触发）
            set({ authEnabled: true, authUser: null });
          }
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
          set({ connected: false, error: '连接失败，请确认 DoAI 工作台后端 agent-cluster serve 已启动' });
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
      name: 'doai-workbench',
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        serverUrl: state.serverUrl,
        authToken: state.authToken,
        accessToken: state.accessToken,
        refreshToken: state.refreshToken,
        authUser: state.authUser,
        authEnabled: state.authEnabled,
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