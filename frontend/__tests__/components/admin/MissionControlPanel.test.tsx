import { StrictMode } from 'react';
import { createHash, webcrypto } from 'node:crypto';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import MissionControlPanel from '../../../components/admin/MissionControlPanel';
import { useWorkspaceStore } from '../../../stores/workspaceStore';
import { useAuthStore } from '../../../stores/authStore';

vi.mock('next/dynamic', () => ({ default: () => (props: { text: string }) => <pre>{props.text}</pre> }));
const noop = () => undefined;
const authHeaders = () => ({ Authorization: `Bearer ${useAuthStore.getState().token}` });
const text = 'STALE PRIVATE TEXT 中文';
const artifact = { id: 'art-private', missionId: 'mis-1', workUnitId: 'wu-1', attempt: 1, kind: 'file',
  mediaType: 'text/plain', sizeBytes: Buffer.byteLength(text), digest: `sha256:${createHash('sha256').update(text).digest('hex')}` };
const payload = { schemaVersion: 1, artifact, text, encoding: 'utf-8', integrity: 'verified' };
function response(value: unknown) { return new Response(JSON.stringify(value), { headers: { 'content-type': 'application/json' } }); }
function routes(preview: (options: RequestInit) => Promise<Response>) {
  return vi.fn(async (input: string, options: RequestInit) => {
    if (input.endsWith('/preview')) return preview(options);
    if (input.includes('/missions?')) return response({ missions: [{ id: 'mis-1', title: 'Mission one', objective: 'Verify real output', status: 'RUNNING', workspaceId: useWorkspaceStore.getState().currentWorkspaceId }] });
    if (input.includes('/work-units?')) return response({ workUnits: [{ id: 'wu-1', missionId: 'mis-1', kind: 'desktop.task', status: 'VERIFYING', attempt: 1 }] });
    if (input.includes('/artifacts?')) return response({ artifacts: [artifact] });
    if (input.includes('/evidence?')) return response({ evidence: [] });
    return response({ events: [] });
  });
}
beforeEach(() => {
  useWorkspaceStore.setState({ currentWorkspaceId: 'workspace-one' });
  useAuthStore.setState({ token: 'one', user: null });
  vi.stubGlobal('crypto', webcrypto);
  vi.stubGlobal('EventSource', class { onmessage = null; onerror = null; close() {} });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it.each(['workspace', 'credential'])('synchronously closes preview on %s change and rejects a cancellation-resistant response', async scope => {
  let resolve!: (value: Response) => void; let signal!: AbortSignal;
  vi.stubGlobal('fetch', routes(options => { signal = options.signal!; return new Promise<Response>(done => { resolve = done; }); }));
  render(<StrictMode><MissionControlPanel authHeaders={authHeaders} setNotice={noop} /></StrictMode>);
  fireEvent.click(await screen.findByRole('button', { name: '预览 art-private' }));
  await waitFor(() => expect(signal).toBeDefined());
  act(() => { if (scope === 'workspace') useWorkspaceStore.setState({ currentWorkspaceId: 'workspace-two' }); else useAuthStore.setState({ token: 'two' }); });
  expect(signal.aborted).toBe(true); expect(screen.queryByRole('dialog')).toBeNull();
  await act(async () => resolve(response(payload)));
  expect(screen.queryByText(text)).toBeNull(); expect(screen.queryByRole('dialog')).toBeNull();
});

it('clears already verified bytes when authenticated context changes', async () => {
  vi.stubGlobal('fetch', routes(async () => response(payload)));
  render(<MissionControlPanel authHeaders={authHeaders} setNotice={noop} />);
  fireEvent.click(await screen.findByRole('button', { name: '预览 art-private' }));
  expect(await screen.findByText(text)).toBeVisible();
  act(() => useAuthStore.setState({ token: '' }));
  expect(screen.queryByText(text)).toBeNull(); expect(screen.queryByRole('dialog')).toBeNull();
});

it('shows malformed detail projections as a retryable read error instead of an empty success', async () => {
  const normal = routes(async () => response(payload));
  vi.stubGlobal('fetch', vi.fn((input: string, options: RequestInit) => input.includes('/artifacts?') ? response({ artifacts: [null] }) : normal(input, options)));
  render(<MissionControlPanel authHeaders={authHeaders} setNotice={noop} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Mission 投影响应格式无效');
  expect(screen.queryByText('暂无 Artifact')).toBeNull();
  expect(screen.getByRole('button', { name: '重试读取详情' })).toBeVisible();
});
