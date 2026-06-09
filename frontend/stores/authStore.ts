import { create } from 'zustand';
import type { User } from '../types';

interface AuthState {
  user: User | null;
  token: string;
  setUser: (u: User | null) => void;
  setToken: (t: string) => void;
  authHeaders: () => Record<string, string>;
  fmtErr: (detail: unknown, fallback: string) => string;
}

export const useAuthStore = create<AuthState>()((set, get) => ({
  user: null,
  token: '',

  setUser: (u) => set({ user: u }),

  setToken: (t) => set({ token: t }),

  authHeaders: (): Record<string, string> => {
    if (typeof window === 'undefined') return {};
    const token = get().token || localStorage.getItem('agenthub_token') || '';
    return token ? { Authorization: `Bearer ${token}` } : {} as Record<string, string>;
  },

  fmtErr: (detail: unknown, fallback: string): string => {
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      return (detail as Array<{ msg?: string }>)
        .map((d) => d.msg || '')
        .filter(Boolean)
        .join('; ') || fallback;
    }
    return fallback;
  },
}));
