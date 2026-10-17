import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AgentHubIM from '../../app/page';
import { useAuthStore } from '../../stores/authStore';

vi.mock('../../components/chat/NativeMissionWorkspace', () => ({ default: ({ token, user, onLogout }: { token: string; user: { name: string }; onLogout: () => void }) => {
  const [draft, setDraft] = useState('');
  return <div><p>{user.name}</p><input aria-label="scope draft" value={draft} onChange={event => setDraft(event.target.value)} /><button onClick={onLogout}>退出登录</button><span data-testid="token-available">{token ? '已登录' : '无身份'}</span></div>;
} }));
const user = { id: 'alice', name: 'Alice', role: 'developer' };
const save = () => { localStorage.setItem('agenthub_token', 'token-alice'); localStorage.setItem('agenthub_user', JSON.stringify(user)); };
beforeEach(() => { localStorage.clear(); useAuthStore.setState({ token: '', user: null }); vi.stubGlobal('fetch', vi.fn()); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('Native Mission entry identity', () => {
  it('renders the login form without transport when no credentials exist', async () => {
    render(<AgentHubIM />);
    expect(await screen.findByRole('heading', { name: 'AgentHub Login' })).toBeVisible();
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each(['{invalid', JSON.stringify({ name: 'missing durable identity', role: 'developer' })])('rejects malformed stored identity without a page crash or API call', async value => {
    localStorage.setItem('agenthub_token', 'token-alice'); localStorage.setItem('agenthub_user', value);
    render(<AgentHubIM />);
    await screen.findByRole('heading', { name: 'AgentHub Login' });
    expect(localStorage.getItem('agenthub_token')).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('hydrates validated credentials and completely replaces content on role/token changes', async () => {
    save(); render(<AgentHubIM />);
    const input = await screen.findByLabelText('scope draft');
    fireEvent.change(input, { target: { value: 'private bytes' } });
    act(() => useAuthStore.setState({ user: { ...user, role: 'viewer' } }));
    expect(screen.getByLabelText('scope draft')).toHaveValue('');
    fireEvent.change(screen.getByLabelText('scope draft'), { target: { value: 'viewer bytes' } });
    act(() => useAuthStore.setState({ token: 'replacement-token' }));
    expect(screen.getByLabelText('scope draft')).toHaveValue('');
    expect(document.body).not.toHaveTextContent('private bytes');
  });

  it('clears credentials and unmounts all observations on logout', async () => {
    save(); render(<AgentHubIM />);
    fireEvent.click(await screen.findByRole('button', { name: '退出登录' }));
    await screen.findByRole('heading', { name: 'AgentHub Login' });
    expect(localStorage.getItem('agenthub_token')).toBeNull();
    expect(localStorage.getItem('agenthub_user')).toBeNull();
    expect(useAuthStore.getState().user).toBeNull();
    expect(screen.queryByLabelText('scope draft')).toBeNull();
  });

  it('keeps failed authentication visible and never manufactures credentials', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('<html>untrusted raw error</html>', { status: 503 }));
    render(<AgentHubIM />);
    fireEvent.change(await screen.findByLabelText('Username'), { target: { value: 'alice' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'password' } });
    fireEvent.click(screen.getByRole('button', { name: 'Login', exact: true }));
    await screen.findByText('身份验证失败（HTTP 503）');
    expect(screen.getByLabelText('Password')).toHaveValue('password');
    expect(localStorage.getItem('agenthub_token')).toBeNull();
    expect(document.body).not.toHaveTextContent('untrusted raw error');
  });

  it('only admits a validated authenticated response and clears the password', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ accessToken: 'actual-token', user })));
    render(<AgentHubIM />);
    fireEvent.change(await screen.findByLabelText('Username'), { target: { value: 'alice' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'password' } });
    fireEvent.click(screen.getByRole('button', { name: 'Login', exact: true }));
    await screen.findByText('Alice');
    expect(useAuthStore.getState().token).toBe('actual-token');
    expect(screen.queryByLabelText('Password')).toBeNull();
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe('/api/auth/login');
  });
});
