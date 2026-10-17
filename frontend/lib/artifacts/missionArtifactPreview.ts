/** Registered Artifact projection; neither byte integrity nor rendering accepts a Mission. */
export const MAX_PREVIEW_BYTES = 262_144;
const MAX_RESPONSE_BYTES = 2_097_152;
const KINDS = ['diff', 'commit', 'file', 'log', 'report', 'test-result', 'build', 'pull-request'];
const METADATA_KEYS = ['id', 'missionId', 'workUnitId', 'attempt', 'kind', 'mediaType', 'sizeBytes', 'digest'];

export interface RegisteredArtifact {
  id: string;
  missionId: string;
  workUnitId: string;
  attempt: number;
  kind: string;
  mediaType: string;
  sizeBytes: number;
  digest: string;
  sensitivity?: string;
}

export interface ArtifactPreview {
  schemaVersion: 1;
  artifact: RegisteredArtifact;
  encoding: 'utf-8';
  integrity: 'verified';
  text: string;
}

function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

function identifier(value: unknown): value is string {
  return typeof value === 'string' && value.length <= 255 && /^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(value);
}

export function registeredArtifact(value: unknown, missionId: string): RegisteredArtifact | null {
  if (!identifier(missionId) || !record(value) || !identifier(value.id) || value.missionId !== missionId
      || !identifier(value.workUnitId) || !Number.isSafeInteger(value.attempt) || Number(value.attempt) < 1
      || typeof value.kind !== 'string' || !KINDS.includes(value.kind)
      || typeof value.mediaType !== 'string' || !value.mediaType || value.mediaType.length > 255
      || !Number.isSafeInteger(value.sizeBytes) || Number(value.sizeBytes) < 0
      || typeof value.digest !== 'string' || !/^sha256:[a-fA-F0-9]{64}$/.test(value.digest)) return null;
  return value as unknown as RegisteredArtifact;
}

export function previewUnavailable(artifact: RegisteredArtifact): string | null {
  if (!['public', 'internal'].includes(artifact.sensitivity ?? 'internal')) return '此敏感级别暂不支持预览';
  if (artifact.sizeBytes > MAX_PREVIEW_BYTES) return '超过 256 KiB，暂不支持预览';
  const [category, subtype, extra] = artifact.mediaType.split(';', 1)[0].trim().toLowerCase().split('/');
  const validSubtype = !extra && !!subtype && /^[a-z0-9!#$%&'*+.^_`|~-]+$/.test(subtype);
  if (!(validSubtype && (category === 'text' || (category === 'application'
      && (['json', 'javascript', 'x-python'].includes(subtype) || subtype.endsWith('+json')))))) return '此文件类型暂不支持预览';
  return null;
}

const HTTP_ERRORS: Record<number, string> = {
  401: '登录已失效，请重新登录', 403: '没有权限预览此产物', 404: '产物不存在或不属于当前 Mission',
  409: '产物字节与登记信息不一致，已停止预览', 413: '产物超过 256 KiB，暂不支持预览',
  415: '此产物不是可预览的 UTF-8 文本', 503: '产物内容暂不可用，请稍后重试',
};

async function boundedJson(response: Response, signal: AbortSignal): Promise<unknown> {
  if (!response.body) throw new Error('产物响应没有内容');
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  const abort = () => { void reader.cancel().catch(() => undefined); };
  signal.addEventListener('abort', abort, { once: true });
  try {
    for (;;) {
      if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
      const { value, done } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > MAX_RESPONSE_BYTES) throw new Error('产物响应超过读取上限');
      chunks.push(value);
    }
    if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  } finally {
    signal.removeEventListener('abort', abort);
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

function exactKeys(value: Record<string, unknown>, keys: string[]): boolean {
  return Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
}

export async function readArtifactPreview(
  expected: RegisteredArtifact,
  headers: Record<string, string>,
  signal: AbortSignal,
): Promise<ArtifactPreview> {
  if (!registeredArtifact(expected, expected.missionId) || previewUnavailable(expected)) throw new Error('此产物暂不支持预览');
  const response = await fetch(`/api/v1/missions/${encodeURIComponent(expected.missionId)}/artifacts/${encodeURIComponent(expected.id)}/preview`, {
    headers: { Accept: 'application/json', ...headers }, signal, cache: 'no-store',
    credentials: 'same-origin', redirect: 'error',
  });
  if (!response.ok) {
    void response.body?.cancel().catch(() => undefined);
    throw new Error(HTTP_ERRORS[response.status] ?? `产物读取失败 (HTTP ${response.status})`);
  }
  if (!/^application\/json(?:;|$)/i.test(response.headers.get('content-type') ?? '')) {
    void response.body?.cancel().catch(() => undefined);
    throw new Error('产物响应格式无效');
  }
  const value = await boundedJson(response, signal);
  const invalid = '产物响应与登记信息不一致';
  if (!record(value) || !exactKeys(value, ['schemaVersion', 'artifact', 'encoding', 'integrity', 'text'])
      || value.schemaVersion !== 1 || value.encoding !== 'utf-8' || value.integrity !== 'verified'
      || typeof value.text !== 'string' || !record(value.artifact) || !exactKeys(value.artifact, METADATA_KEYS)) throw new Error(invalid);
  const actual = registeredArtifact(value.artifact, expected.missionId);
  if (!actual || METADATA_KEYS.some(key => value.artifact && (value.artifact as Record<string, unknown>)[key] !== (expected as unknown as Record<string, unknown>)[key])) throw new Error(invalid);
  const bytes = new TextEncoder().encode(value.text);
  if (bytes.length > MAX_PREVIEW_BYTES || bytes.length !== expected.sizeBytes || value.text.includes('\0')
      || new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes) !== value.text) throw new Error(invalid);
  const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), byte => byte.toString(16).padStart(2, '0')).join('');
  if (`sha256:${hash}` !== expected.digest.toLowerCase()) throw new Error(invalid);
  if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
  return value as unknown as ArtifactPreview;
}
