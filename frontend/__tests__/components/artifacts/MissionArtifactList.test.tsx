import { createHash, webcrypto } from 'node:crypto';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MissionArtifactList } from '../../../components/artifacts/MissionArtifactList';

vi.mock('next/dynamic', () => ({ default: () => (props: { text: string }) => <pre data-testid="artifact-code">{props.text}</pre> }));
const headers = () => ({ Authorization: 'Bearer fixture' });
function fixture(text = '真正的产物 <script>window.BAD=true</script>') {
  const artifact = { id: 'art-1', missionId: 'mis-1', workUnitId: 'wu-1', attempt: 1, kind: 'file',
    mediaType: 'text/plain', sizeBytes: Buffer.byteLength(text), digest: `sha256:${createHash('sha256').update(text).digest('hex')}` };
  return { artifact, payload: { schemaVersion: 1, artifact, text, encoding: 'utf-8', integrity: 'verified' } };
}
function reply(payload: unknown, status = 200) { return new Response(JSON.stringify(payload), { status, headers: { 'content-type': 'application/json' } }); }
function open() { fireEvent.click(screen.getByRole('button', { name: '预览 art-1' })); }
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

it('reads only on explicit opening, labels integrity separately and renders literal text', async () => {
  const { artifact, payload } = fixture(); const fetch = vi.fn().mockResolvedValue(reply(payload));
  vi.stubGlobal('fetch', fetch); vi.stubGlobal('crypto', webcrypto);
  render(<MissionArtifactList missionId="mis-1" items={[artifact]} authHeaders={headers} />);
  expect(fetch).not.toHaveBeenCalled(); open();
  expect(await screen.findByTestId('artifact-code')).toHaveTextContent(payload.text);
  expect(screen.getByText(/不代表任务验收通过/)).toBeVisible();
  expect(document.querySelector('script')).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: '关闭产物预览' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(screen.getByRole('button', { name: '预览 art-1' })).toHaveFocus();
});

it('renders genuine empty content without inventing a code sample', async () => {
  const { artifact, payload } = fixture(''); vi.stubGlobal('crypto', webcrypto);
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply(payload)));
  render(<MissionArtifactList missionId="mis-1" items={[artifact]} authHeaders={headers} />); open();
  expect(await screen.findByText('空产物（0 字节）')).toBeVisible();
  expect(screen.queryByTestId('artifact-code')).toBeNull();
});

it('keeps read failures visible and retries only the read command', async () => {
  const { artifact, payload } = fixture(); const fetch = vi.fn().mockResolvedValueOnce(reply({}, 503)).mockResolvedValueOnce(reply(payload));
  vi.stubGlobal('fetch', fetch); vi.stubGlobal('crypto', webcrypto);
  render(<MissionArtifactList missionId="mis-1" items={[artifact]} authHeaders={headers} />); open();
  expect(await screen.findByRole('alert')).toHaveTextContent('产物内容暂不可用');
  expect(screen.queryByTestId('artifact-code')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '重试读取产物' }));
  expect(await screen.findByTestId('artifact-code')).toBeVisible();
  expect(fetch.mock.calls.every(([, options]) => !options.method)).toBe(true);
});

it('aborts close and ignores a port that returns after unmount or scope replacement', async () => {
  const { artifact, payload } = fixture(); let resolve!: (response: Response) => void; let signal!: AbortSignal;
  vi.stubGlobal('crypto', webcrypto);
  vi.stubGlobal('fetch', vi.fn().mockImplementation((_url, options) => { signal = options.signal; return new Promise<Response>(done => { resolve = done; }); }));
  const view = render(<MissionArtifactList key="scope-1" missionId="mis-1" items={[artifact]} authHeaders={headers} />); open();
  await waitFor(() => expect(signal).toBeDefined());
  view.rerender(<MissionArtifactList key="scope-2" missionId="other-mission" items={[]} authHeaders={headers} />);
  expect(signal.aborted).toBe(true);
  await act(async () => resolve(reply(payload)));
  expect(screen.queryByRole('dialog')).toBeNull(); expect(screen.queryByText(payload.text)).toBeNull();
});

it('shows a terminal read timeout even if a transport ignores cancellation', async () => {
  vi.useFakeTimers();
  const { artifact } = fixture();
  vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => undefined)));
  render(<MissionArtifactList missionId="mis-1" items={[artifact]} authHeaders={headers} />); open();
  act(() => vi.advanceTimersByTime(15000));
  expect(screen.getByRole('alert')).toHaveTextContent('产物读取超时，请重试');
  expect(screen.queryByTestId('artifact-code')).toBeNull();
});
