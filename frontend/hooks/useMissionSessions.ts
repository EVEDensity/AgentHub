import { useCallback, useEffect, useRef, useState } from 'react';
import { readSessionPage, type NativeSession, type SessionMission, type SessionReadScope, type SessionPageKind, type PageCursor } from '../lib/missionSessions';

interface Snapshot<T> {
  scope: string; items: T[]; next: PageCursor; loading: boolean; error: string | null; limited: boolean;
}

/** Bounded disposable read cache. Every scope transition invalidates old reads. */
function useNativePages<T extends { id: string }>(kind: SessionPageKind, options: SessionReadScope) {
  const { token, workspaceId, sessionId } = options;
  const scope = JSON.stringify([kind, token, workspaceId, sessionId]);
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const [snapshot, setSnapshot] = useState<Snapshot<T> | null>(null);
  const snapshotRef = useRef(snapshot);
  snapshotRef.current = snapshot;
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const busy = useRef(false);
  const maximum = kind === 'sessions' ? 500 : 200;

  const run = useCallback(async (reset: boolean) => {
    const previous = snapshotRef.current?.scope === scope ? snapshotRef.current : null;
    if (!token || !workspaceId || kind === 'missions' && !sessionId || !reset && (busy.current || previous?.next == null || previous.limited)) return;
    controller.current?.abort();
    const request = ++generation.current;
    const abort = new AbortController();
    controller.current = abort;
    busy.current = true;
    const initial: Snapshot<T> = { scope, items: reset ? [] : previous?.items ?? [], next: reset ? null : previous?.next ?? null, loading: true, error: null, limited: false };
    setSnapshot(initial);
    snapshotRef.current = initial;
    const current = () => scopeRef.current === scope && generation.current === request;
    try {
      const page = await readSessionPage(kind, { token, workspaceId, sessionId }, reset ? null : initial.next, abort);
      if (!current() || abort.signal.aborted) return;
      const byId = new Map(initial.items.map(item => [item.id, item]));
      for (const item of page.items) byId.set(item.id, item as unknown as T);
      const items = [...byId.values()];
      const limited = items.length > maximum || items.length === maximum && page.next !== null;
      const next = { scope, items: items.slice(0, maximum), next: page.next, limited, loading: false, error: null };
      snapshotRef.current = next;
      setSnapshot(next);
    } catch (error) {
      if (!current()) return;
      const next = { ...initial, loading: false, error: error instanceof Error ? error.message : '会话读取失败' };
      snapshotRef.current = next;
      setSnapshot(next);
    } finally { if (current()) busy.current = false; }
  }, [scope, kind, token, workspaceId, sessionId, maximum]);

  useEffect(() => {
    void run(true);
    return () => { ++generation.current; controller.current?.abort(); busy.current = false; };
  }, [run]);

  const reload = useCallback(() => { void run(true); }, [run]);
  const loadMore = useCallback(() => { void run(false); }, [run]);
  const visible = snapshot?.scope === scope ? snapshot : null;
  return { items: visible?.items ?? [], loading: visible?.loading ?? false, error: visible?.error ?? null,
    hasMore: visible?.next != null && !visible.limited, limited: visible?.limited ?? false, reload, loadMore };
}

export function useMissionSessions(options: SessionReadScope) { return useNativePages<NativeSession>('sessions', options); }
export function useSessionMissions(options: SessionReadScope) { return useNativePages<SessionMission>('missions', options); }
