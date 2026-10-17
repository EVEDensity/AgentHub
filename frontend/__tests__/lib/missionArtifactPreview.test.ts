import { createHash, webcrypto } from 'node:crypto';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { readArtifactPreview, registeredArtifact, previewUnavailable, type RegisteredArtifact } from '../../lib/artifacts/missionArtifactPreview';

function fixture(text = '产物 中文\n') {
  const artifact: RegisteredArtifact = { id: 'artifact-1', missionId: 'mis-1', workUnitId: 'wu-1', attempt: 2,
    kind: 'file', mediaType: 'text/plain', sizeBytes: Buffer.byteLength(text), digest: `sha256:${createHash('sha256').update(text).digest('hex')}` };
  return { artifact, payload: { schemaVersion: 1, artifact, encoding: 'utf-8', integrity: 'verified', text } };
}
function reply(value: unknown) { return new Response(JSON.stringify(value), { headers: { 'content-type': 'application/json' } }); }
async function read(artifact: RegisteredArtifact) {
  vi.stubGlobal('crypto', webcrypto);
  return readArtifactPreview(artifact, { Authorization: 'Bearer fixture' }, new AbortController().signal);
}
afterEach(() => vi.unstubAllGlobals());

describe('registered Artifact preview transport', () => {
  it.each(['产物 中文\n', '', '\ufeffBOM preserved', '<script>window.PREVIEW_EXECUTED=true</script>'])('checks actual UTF-8 bytes and SHA-256 without rendering content: %j', async text => {
    const { artifact, payload } = fixture(text);
    const fetch = vi.fn().mockResolvedValue(reply(payload)); vi.stubGlobal('fetch', fetch);
    expect((await read(artifact)).text).toBe(text);
    expect(fetch).toHaveBeenCalledWith('/api/v1/missions/mis-1/artifacts/artifact-1/preview', expect.objectContaining({ cache: 'no-store', redirect: 'error', credentials: 'same-origin', headers: { Accept: 'application/json', Authorization: 'Bearer fixture' } }));
  });

  it('rejects altered scope, bytes, metadata, extra fields and invented acceptance', async () => {
    const { artifact, payload } = fixture();
    const bad = [
      { ...payload, text: 'different' }, { ...payload, artifact: { ...artifact, attempt: 3 } },
      { ...payload, artifact: { ...artifact, missionId: 'other-mission' } },
      { ...payload, artifact: { ...artifact, contentAddress: 'private' } },
      { ...payload, verdict: 'PASS' }, { ...payload, schemaVersion: '1' },
      { ...payload, integrity: 'PASS' }, { ...payload, encoding: 'base64' },
      { ...payload, text: '\ud800' }, { ...payload, text: 'x'.repeat(262145) },
    ];
    for (const value of bad) {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply(value)));
      await expect(read(artifact)).rejects.toThrow();
    }
    const bytes = fixture('same byte length');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply({ ...bytes.payload, text: 'wrong text size!' })));
    await expect(read(bytes.artifact)).rejects.toThrow();
  });

  it('refuses unsupported and excessive metadata before any HTTP request', async () => {
    const { artifact } = fixture(); const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    expect(registeredArtifact({ ...artifact, attempt: 0 }, 'mis-1')).toBeNull();
    expect(registeredArtifact({ ...artifact, missionId: 'other' }, 'mis-1')).toBeNull();
    for (const value of [{ ...artifact, sizeBytes: 262145 }, { ...artifact, mediaType: 'image/png' }, { ...artifact, sensitivity: 'restricted' }]) {
      expect(previewUnavailable(value)).not.toBeNull();
      await expect(read(value)).rejects.toThrow();
    }
    expect(fetch).not.toHaveBeenCalled();
  });

  it('does not include server error bodies or storage paths in visible HTTP errors', async () => {
    const { artifact } = fixture();
    for (const status of [401, 403, 404, 409, 413, 415, 503]) {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('private/path/secret', { status })));
      await expect(read(artifact)).rejects.toThrow();
      try { await read(artifact); } catch (error) { expect(String(error)).not.toContain('private'); }
    }
  });

  it('cancels unexpected or oversized response streams', async () => {
    const { artifact } = fixture();
    const cancel = vi.fn();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new ReadableStream({ cancel }), { headers: { 'content-type': 'text/html' } })));
    await expect(read(artifact)).rejects.toThrow('格式无效'); expect(cancel).toHaveBeenCalled();
    const oversizedCancel = vi.fn();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array(2097153)); }, cancel: oversizedCancel }), { headers: { 'content-type': 'application/json' } })));
    await expect(read(artifact)).rejects.toThrow('超过读取上限'); expect(oversizedCancel).toHaveBeenCalled();
  });

  it('does not return successful bytes after cancellation', async () => {
    const { artifact, payload } = fixture(); const controller = new AbortController();
    vi.stubGlobal('crypto', webcrypto);
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async () => { controller.abort(); return reply(payload); }));
    await expect(readArtifactPreview(artifact, {}, controller.signal)).rejects.toThrow();
  });
});
