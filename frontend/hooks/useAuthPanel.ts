'use client';

// ────────────────────────────────────────────────────────────────────
// 登录/注册表单 + 会话凭证状态（从 app/page.tsx 抽出，行为保持一致）
// WebSocket 依赖已在 T0-3 移除；登出/凭证过期现在直接遍历 sessions。
// ────────────────────────────────────────────────────────────────────
import { useCallback, useEffect, useState } from 'react';
import type { ChatSession, User } from '../types';

export interface UseAuthPanelOptions {
  sessions: ChatSession[];
  clearSession: (sessionId: string) => void;
  clearDagSession: (sessionId: string) => void;
  setNotice: (msg: string) => void;
}

export function useAuthPanel({
  sessions,
  clearSession,
  clearDagSession,
  setNotice,
}: UseAuthPanelOptions) {
  const [token, setToken] = useState<string>('');
  const [user, setUser] = useState<User | null>(null);
  const [authMode, setAuthMode] = useState<'login' | 'register'>('login');
  const [authForm, setAuthForm] = useState<{ name: string; password: string }>({ name: '', password: '' });

  // 启动时从 localStorage 恢复上次会话凭证
  useEffect(() => {
    const saved = localStorage.getItem('agenthub_token');
    const savedUser = localStorage.getItem('agenthub_user');
    if (saved) setToken(saved);
    if (savedUser) setUser(JSON.parse(savedUser) as User);
  }, []);

  const handleAuthFormChange = useCallback((update: Partial<{ name: string; password: string }>) => {
    setAuthForm((prev) => ({ ...prev, ...update }));
  }, []);

  const handleToggleAuthMode = useCallback(() => {
    setAuthMode((prev) => (prev === 'login' ? 'register' : 'login'));
  }, []);

  const handleAuthSubmit = useCallback((e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const submit = async () => {
      const res = await fetch(`/api/auth/${authMode}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(authForm),
      });
      const data = await res.json();
      if (!res.ok) {
        setNotice(data.detail || 'Auth failed');
        return;
      }
      localStorage.setItem('agenthub_token', data.accessToken);
      localStorage.setItem('agenthub_user', JSON.stringify(data.user));
      setToken(data.accessToken as string);
      setUser(data.user as User);
      setNotice('Login success');
    };
    void submit();
  }, [authMode, authForm, setNotice]);

  /** Centralised handler for expired / invalid auth tokens.
   *  Clears stored credentials and session store,
   *  and returns the UI to the login screen with an explanatory notice. */
  const handleTokenExpired = useCallback((): void => {
    // Prevent duplicate logout cascades
    if (!localStorage.getItem('agenthub_token')) return;

    const sessionIds = sessions.map((s) => s.id);
    localStorage.removeItem('agenthub_token');
    localStorage.removeItem('agenthub_user');
    sessionIds.forEach((sid) => clearSession(sid));
    sessionIds.forEach((sid) => clearDagSession(sid));
    setToken('');
    setUser(null);
    setNotice('登录已过期，请重新登录');
  }, [sessions, clearSession, clearDagSession, setNotice]);

  const handleLogout = useCallback(() => {
    const sessionIds = sessions.map((s) => s.id);
    localStorage.removeItem('agenthub_token');
    localStorage.removeItem('agenthub_user');
    sessionIds.forEach((sid) => clearSession(sid));
    sessionIds.forEach((sid) => clearDagSession(sid));
    setToken('');
    setUser(null);
  }, [sessions, clearSession, clearDagSession]);

  return {
    token,
    user,
    authMode,
    authForm,
    handleAuthFormChange,
    handleToggleAuthMode,
    handleAuthSubmit,
    handleTokenExpired,
    handleLogout,
  };
}
