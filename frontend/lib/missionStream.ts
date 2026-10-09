import { mapMissionEvent, type ChatRenderEvent } from './missionEventMapper';
import type { MissionEvent } from '../types';

export async function readMissionStream(
  url: string,
  headers: Record<string, string>,
  signal: AbortSignal,
  onEvent: (event: ChatRenderEvent) => void,
  onConnected?: () => void,
): Promise<void> {
  const response = await fetch(url, { headers, signal });
  if (!response.ok || !response.body) throw new Error(`事件连接失败 (HTTP ${response.status})`);
  if (signal.aborted) { await response.body.cancel().catch(() => undefined); return; }
  onConnected?.();
  const reader = response.body.getReader();
  const abort = () => { void reader.cancel().catch(() => undefined); };
  signal.addEventListener('abort', abort, { once: true });
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (!signal.aborted) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split('\n\n');
      buffer = frames.pop() ?? '';
      for (const frame of frames) deliver(frame, signal, onEvent);
    }
    if (buffer.trim()) deliver(buffer, signal, onEvent);
  } finally {
    signal.removeEventListener('abort', abort);
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

function deliver(frame: string, signal: AbortSignal, onEvent: (event: ChatRenderEvent) => void): void {
  if (signal.aborted) return;
  const data = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trim()).join('\n');
  if (!data) return;
  try {
    const raw = JSON.parse(data) as MissionEvent;
    const event = mapMissionEvent(raw);
    if (event) onEvent({ ...event, eventId: String(raw.eventId ?? raw.event_id ?? '') });
  } catch {
    // Invalid frames cannot establish Mission or execution state.
  }
}

export async function missionCommand(path: string, body: unknown, headers: Record<string, string>): Promise<Record<string, any>> {
  const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败 (HTTP ${response.status})`);
  return data;
}
