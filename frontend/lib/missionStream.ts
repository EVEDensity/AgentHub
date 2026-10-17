import { mapMissionEvent, type ChatRenderEvent } from './missionEventMapper';
import type { MissionEvent } from '../types';

const MAX_FRAME_BYTES = 262_144;
export const missionIdentifier = (value: unknown): value is string => typeof value === 'string'
  && value.length <= 255 && /^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(value);

export function missionStreamPath(url: unknown, missionId: string): string {
  if (typeof url !== 'string' || url.length > 2048 || !url.startsWith('/') || url.startsWith('//') || url.includes('\\')) throw new Error('任务事件地址无效');
  const parsed = new URL(url, 'https://mission.invalid');
  if (!missionIdentifier(missionId) || parsed.origin !== 'https://mission.invalid'
      || parsed.pathname !== `/api/v1/missions/${encodeURIComponent(missionId)}/events/stream` || parsed.hash) throw new Error('任务事件地址无效');
  return parsed.pathname + parsed.search;
}

/** Bound transport bytes before JSON parsing; never interpolate a raw error page. */
export async function readMissionJson(response: Response, signal?: AbortSignal): Promise<Record<string, any>> {
  if (!response.body) throw new Error('任务响应格式无效');
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  const abort = () => { void reader.cancel().catch(() => undefined); };
  signal?.addEventListener('abort', abort, { once: true });
  try {
    for (;;) {
      if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
      const { value, done } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > MAX_FRAME_BYTES) throw new Error('任务响应超过读取上限');
      chunks.push(value);
    }
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    let value: unknown;
    try { value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
    catch { throw new Error('任务响应格式无效'); }
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('任务响应格式无效');
    return value as Record<string, any>;
  } finally {
    signal?.removeEventListener('abort', abort);
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

export interface MissionStreamOptions { missionId?: string; afterEventId?: string; requireEventId?: boolean; }

export async function readMissionStream(
  url: string, headers: Record<string, string>, signal: AbortSignal,
  onEvent: (event: ChatRenderEvent) => void, onConnected?: () => void,
  options: MissionStreamOptions = {},
): Promise<void> {
  const inferred = /^\/api\/v1\/missions\/([^/?#]+)\/events\/stream(?:\?|$)/.exec(url)?.[1];
  const target = options.missionId ?? inferred;
  if (!target) throw new Error('任务事件地址无效');
  const path = missionStreamPath(url, target);
  const cursor = options.afterEventId;
  if (cursor && !missionIdentifier(cursor)) throw new Error('任务事件游标无效');
  const response = await fetch(path, {
    headers: { ...headers, ...(cursor ? { 'Last-Event-ID': cursor } : {}) }, signal,
    cache: 'no-store', credentials: 'same-origin', redirect: 'error',
  });
  if (!response.ok || !response.body) { void response.body?.cancel().catch(() => undefined); throw new Error(`事件连接失败 (HTTP ${response.status})`); }
  if (options.requireEventId && !/^text\/event-stream(?:;|$)/i.test(response.headers.get('content-type') ?? '')) {
    void response.body.cancel().catch(() => undefined);
    throw new Error('事件响应格式无效');
  }
  if (signal.aborted) { await response.body.cancel().catch(() => undefined); return; }
  onConnected?.();
  const reader = response.body.getReader();
  const abort = () => { void reader.cancel().catch(() => undefined); };
  signal.addEventListener('abort', abort, { once: true });
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let buffer = '';
  try {
    while (!signal.aborted) {
      const { value, done } = await reader.read();
      if (done) { buffer += decoder.decode(); break; }
      // Coalesced valid frames may share one chunk; the residual frame is bounded separately.
      if (value.byteLength > 2_097_152) throw new Error('事件数据超过读取上限');
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() ?? '';
      for (const frame of frames) deliver(frame, signal, onEvent, { ...options, missionId: target });
      if (new TextEncoder().encode(buffer).length > MAX_FRAME_BYTES) throw new Error('事件帧超过读取上限');
    }
    if (buffer.trim()) deliver(buffer, signal, onEvent, { ...options, missionId: target });
  } finally {
    signal.removeEventListener('abort', abort);
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

function deliver(frame: string, signal: AbortSignal, onEvent: (event: ChatRenderEvent) => void, options: MissionStreamOptions): void {
  if (signal.aborted) return;
  if (new TextEncoder().encode(frame).length > MAX_FRAME_BYTES) throw new Error('事件帧超过读取上限');
  const lines = frame.split(/\r?\n/);
  const data = lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
  if (!data) return;
  let raw: MissionEvent;
  try { raw = JSON.parse(data) as MissionEvent; }
  catch { throw new Error('事件响应格式无效'); }
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('事件响应格式无效');
  const fields = raw as Record<string, unknown>;
  const eventId = fields.eventId ?? fields.event_id ?? lines.find(line => line.startsWith('id:'))?.slice(3).trim();
  const missionId = fields.missionId ?? fields.correlation_id ?? fields.correlationId;
  const eventType = fields.type ?? fields.eventType ?? fields.event_type;
  if (options.missionId && missionId !== options.missionId) throw new Error('事件不属于当前 Mission');
  if (options.requireEventId && !missionIdentifier(eventId)) throw new Error('事件缺少有效登记标识');
  if (typeof eventType !== 'string' || !eventType || eventType.length > 255) throw new Error('事件类型无效');
  const event = mapMissionEvent({ ...raw, missionId: String(missionId ?? ''), type: eventType });
  if (event) onEvent({ ...event, eventType, eventId: typeof eventId === 'string' ? eventId : '' });
}

export async function missionCommand(path: string, body: unknown, headers: Record<string, string>, signal?: AbortSignal): Promise<Record<string, any>> {
  const response = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body),
    signal, cache: 'no-store', credentials: 'same-origin', redirect: 'error',
  });
  const data: Record<string, any> = await readMissionJson(response, signal).catch(error => { if (response.ok) throw error; return {}; });
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail.slice(0, 512) : `请求失败 (HTTP ${response.status})`);
  return data;
}
