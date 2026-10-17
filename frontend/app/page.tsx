'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import AuthForm from '../components/chat/AuthForm';
import NativeMissionWorkspace from '../components/chat/NativeMissionWorkspace';
import { missionIdentifier, readMissionJson } from '../lib/missionStream';
import { useAuthStore } from '../stores/authStore';
import type { User } from '../types';

function authenticatedUser(value: unknown): value is User & { id: string } {
  if (!value || typeof value !== 'object') return false;
  const user = value as Record<string, unknown>;
  return missionIdentifier(user.id) && typeof user.name === 'string' && user.name.length <= 200
    && typeof user.role === 'string' && user.role.length > 0 && user.role.length <= 64;
}

/** Native Mission sessions are read from the control plane, never legacy aliases. */
export default function AgentHubIM() {
  const token = useAuthStore(state => state.token);
  const user = useAuthStore(state => state.user);
  const [hydrated, setHydrated] = useState(false);
  const [authMode, setAuthMode] = useState<'login' | 'register'>('login');
  const [authForm, setAuthForm] = useState({ name: '', password: '' });
  const [notice, setNotice] = useState('');
  const authentication = useRef<AbortController | null>(null);
  const scope = useRef({ parts: [token, user?.id, user?.role], revision: 0 });
  const parts = [token, user?.id, user?.role];
  if (parts.some((part, index) => part !== scope.current.parts[index])) scope.current = { parts, revision: scope.current.revision + 1 };

  const clearCredentials = useCallback(() => {
    authentication.current?.abort();
    authentication.current = null;
    localStorage.removeItem('agenthub_token');
    localStorage.removeItem('agenthub_user');
    useAuthStore.setState({ token: '', user: null });
    setAuthForm({ name: '', password: '' });
  }, []);

  useEffect(() => {
    const hydrate = () => {
      try {
        const savedToken = localStorage.getItem('agenthub_token') ?? '';
        const savedUser: unknown = JSON.parse(localStorage.getItem('agenthub_user') ?? 'null');
        if (savedToken && savedToken.length <= 8192 && !/[\r\n]/.test(savedToken) && authenticatedUser(savedUser)) {
          useAuthStore.setState({ token: savedToken, user: savedUser });
        } else clearCredentials();
      } catch { clearCredentials(); }
      setHydrated(true);
    };
    hydrate();
    const changed = (event: StorageEvent) => {
      if (event.key === null || event.key === 'agenthub_token' || event.key === 'agenthub_user') {
        authentication.current?.abort();
        hydrate();
      }
    };
    window.addEventListener('storage', changed);
    return () => { window.removeEventListener('storage', changed); authentication.current?.abort(); };
  }, [clearCredentials]);

  const authenticate = async () => {
    if (authentication.current) return;
    const controller = new AbortController();
    authentication.current = controller;
    const deadline = setTimeout(() => controller.abort(), 20_000);
    setNotice('正在验证身份…');
    try {
      const response = await fetch(`/api/auth/${authMode}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(authForm), signal: controller.signal, cache: 'no-store', redirect: 'error' });
      if (!response.ok) throw new Error(`身份验证失败（HTTP ${response.status}）`);
      const body = await readMissionJson(response, controller.signal);
      if (!authenticatedUser(body.user) || typeof body.accessToken !== 'string' || !body.accessToken || body.accessToken.length > 8192 || /[\r\n]/.test(body.accessToken)) throw new Error('身份验证响应无效');
      if (controller.signal.aborted) return;
      localStorage.setItem('agenthub_token', body.accessToken);
      localStorage.setItem('agenthub_user', JSON.stringify(body.user));
      useAuthStore.setState({ token: body.accessToken, user: body.user });
      setAuthForm({ name: '', password: '' });
      setNotice('');
    } catch (error) {
      setNotice(controller.signal.aborted ? '身份验证未完成，请重试' : error instanceof Error ? error.message : '身份验证失败');
    } finally {
      clearTimeout(deadline);
      if (authentication.current === controller) authentication.current = null;
    }
  };

  if (!hydrated) return <main className="p-6" role="status">正在读取本地登录凭证…</main>;
  if (!token || !authenticatedUser(user)) return <AuthForm authMode={authMode} authForm={authForm} notice={notice}
    onAuthFormChange={update => setAuthForm(previous => ({ ...previous, ...update }))}
    onToggleMode={() => { setAuthMode(previous => previous === 'login' ? 'register' : 'login'); setNotice(''); }}
    onSubmit={event => { event.preventDefault(); void authenticate(); }} />;
  return <NativeMissionWorkspace key={scope.current.revision} token={token} user={user} onLogout={clearCredentials} />;
}