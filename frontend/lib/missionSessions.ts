import { missionIdentifier, readMissionJson } from './missionStream';

export interface NativeSession {
  id: string; workspaceId: string; title: string; status: 'ACTIVE' | 'ARCHIVED';
  createdAt: string; updatedAt: string;
}
export interface SessionMission {
  id: string; workspaceId: string; sessionId: string; title: string;
  objectivePreview: string; objectiveTruncated: boolean; status: string; createdAt: string;
}
export interface SessionReadScope { token: string; workspaceId: string; sessionId?: string; }
export type SessionPageKind = 'sessions' | 'missions';
export type PageCursor = string | number | null;
export interface NativePage<T> { items: T[]; next: PageCursor; }
const states = ['DRAFT', 'READY', 'RUNNING', 'VERIFYING', 'WAITING_DECISION', 'SUCCEEDED', 'FAILED', 'CANCELLED'];
const date = (v: unknown) => typeof v === 'string' && /T.+(?:Z|[+-]\d{2}:\d{2})$/.test(v) && Number.isFinite(Date.parse(v));
const text = (v: unknown, limit: number) => typeof v === 'string' && Array.from(v).length <= limit;

/** Validate scope and pagination before exposing any server projection. */
export function validateSessionPage(value: Record<string, any>, kind: SessionPageKind, scope: SessionReadScope, cursor: PageCursor): NativePage<NativeSession | SessionMission> {
  const invalid = () => { throw new Error('会话读取响应无效'); };
  if (value.schemaVersion !== 1 || value.workspaceId !== scope.workspaceId || typeof value.hasMore !== 'boolean') return invalid();
  const items = kind === 'sessions' ? value.sessions : value.missions;
  if (!Array.isArray(items) || items.length > (kind === 'sessions' ? 50 : 20)) return invalid();
  const seen = new Set<string>();
  for (const item of items) {
    if (!item || !missionIdentifier(item.id) || seen.has(item.id) || item.workspaceId !== scope.workspaceId
        || !text(item.title, 500) || !date(item.createdAt)) return invalid();
    seen.add(item.id);
    if (kind === 'sessions') {
      if (!['ACTIVE', 'ARCHIVED'].includes(item.status) || !date(item.updatedAt)) return invalid();
    } else if (value.sessionId !== scope.sessionId || item.sessionId !== scope.sessionId || !states.includes(item.status)
        || !text(item.objectivePreview, 1000) || typeof item.objectiveTruncated !== 'boolean') return invalid();
  }
  if (kind === 'missions' && value.sessionId !== scope.sessionId) return invalid();
  const next = kind === 'sessions' ? value.nextOffset : value.nextBeforeId;
  if (!value.hasMore && next !== null) return invalid();
  if (value.hasMore && (items.length === 0 || (kind === 'sessions'
    ? !Number.isSafeInteger(next) || next !== (typeof cursor === 'number' ? cursor : 0) + items.length
    : next !== items[items.length - 1].id || next === cursor))) return invalid();
  return { items, next: value.hasMore ? next : null };
}

/** These reads never admit, retry, resume execution or cancel a Mission. */
export async function readSessionPage(kind: SessionPageKind, scope: SessionReadScope, cursor: PageCursor, controller: AbortController): Promise<NativePage<NativeSession | SessionMission>> {
  if (!missionIdentifier(scope.workspaceId) || kind === 'missions' && !missionIdentifier(scope.sessionId)) throw new Error('请选择有效的原生会话工作空间');
  const query = new URLSearchParams({ workspaceId: scope.workspaceId, limit: kind === 'sessions' ? '50' : '20' });
  if (cursor !== null) query.set(kind === 'sessions' ? 'offset' : 'beforeId', String(cursor));
  const path = kind === 'sessions' ? '/api/v1/sessions' : `/api/v1/sessions/${encodeURIComponent(scope.sessionId!)}/missions`;
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([(async () => {
      const response = await fetch(`${path}?${query}`, { headers: { Authorization: `Bearer ${scope.token}` }, signal: controller.signal,
        credentials: 'same-origin', cache: 'no-store', redirect: 'error' });
      if (!response.ok) { void response.body?.cancel().catch(() => undefined); throw new Error(`会话读取暂不可用 (HTTP ${response.status})`); }
      return validateSessionPage(await readMissionJson(response, controller.signal), kind, scope, cursor);
    })(), new Promise<never>((_, reject) => { timer = setTimeout(() => { controller.abort(); reject(new Error('会话读取超时，请重试')); }, 10_000); })]);
  } finally { clearTimeout(timer); }
}
