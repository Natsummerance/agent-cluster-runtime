import { create } from 'zustand';
import { apiErrorMessage } from './appStore';
import * as api from '../api/endpoints';
import type { DashboardData, TaskEntry } from '../api/types';

export interface ProjectFilters {
  status?: string;
  assignee?: string;
  q?: string;
}

interface ProjectState {
  dashboard: Record<string, DashboardData>;
  tasks: Record<string, TaskEntry[]>;
  filters: ProjectFilters;
  loading: boolean;
  error: string | null;
  loadDashboard(pid: string): Promise<DashboardData | null>;
  loadTasks(pid: string): Promise<TaskEntry[] | null>;
  setFilter(filters: Partial<ProjectFilters>): void;
  assignTask(pid: string, sid: string, assignee: string): Promise<void>;
  resetState(): void;
}

export const useProjectStore = create<ProjectState>()((set, get) => ({
  dashboard: {},
  tasks: {},
  filters: {},
  loading: false,
  error: null,

  async loadDashboard(pid: string) {
    set({ loading: true, error: null });
    try {
      const data = await api.fetchDashboard(pid);
      set((state) => ({ dashboard: { ...state.dashboard, [pid]: data }, loading: false }));
      return data;
    } catch (err) {
      set({ error: apiErrorMessage(err), loading: false });
      return null;
    }
  },

  async loadTasks(pid: string) {
    set({ loading: true, error: null });
    try {
      const tasks = await api.fetchTasks(pid, get().filters);
      set((state) => ({ tasks: { ...state.tasks, [pid]: tasks }, loading: false }));
      return tasks;
    } catch (err) {
      set({ error: apiErrorMessage(err), loading: false });
      return null;
    }
  },

  setFilter(filters: Partial<ProjectFilters>) {
    set((state) => ({ filters: { ...state.filters, ...filters } }));
  },

  async assignTask(pid: string, sid: string, assignee: string) {
    try {
      await api.assignTask(pid, sid, assignee);
      await get().loadTasks(pid);
    } catch (err) {
      set({ error: apiErrorMessage(err) });
      throw err;
    }
  },

  resetState() {
    set({ dashboard: {}, tasks: {}, filters: {}, loading: false, error: null });
  },
}));
