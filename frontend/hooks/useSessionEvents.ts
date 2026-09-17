/**
 * useSessionEvents — subscribe to the SessionEvent SSE stream for a session.
 *
 * Connects to ``GET /api/v1/sessions/{sessionId}/events/stream``,
 * parses each ``data: <json>`` frame into a :class:`SessionEvent`,
 * and exposes them as an append-only array plus connection state.
 *
 * Design notes (from experience "SSE 接口对接"):
 *   - Uses ``fetch + ReadableStream`` instead of ``EventSource`` so
 *     custom auth headers flow through.
 *   - Auto-reconnects on transport errors with exponential backoff
 *     (cap 30s) — but **only** when the consumer is still mounted
 *     and the sessionId has not changed.
 *   - ``afterId`` is carried across reconnects so we never replay
 *     history we've already seen (handled by the backend).
 *   - Debug log behind ``process.env.NODE_ENV === 'development'`` so
 *     the production bundle stays clean.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import type { SessionEvent } from '../types';

export type SessionEventStreamState = 'idle' | 'connecting' | 'streaming' | 'closed' | 'reconnecting' | 'error';

export interface SessionEventStreamOptions {
  token?: string;
  sessionId?: string;
  workspaceId?: string;
  authHeaders: () => Record<string, string>;
  /** Optional filter — only deliver events of this type. */
  eventType?: string;
  /** Poll interval hint (seconds); backend clamps to [0.1, 30]. */
  pollSeconds?: number;
  /** Called once per new event — cheap bridge for toast / analytics. */
  onEvent?: (event: SessionEvent) => void;
}

export interface SessionEventStreamHandle {
  events: SessionEvent[];
  state: SessionEventStreamState;
  /** Last successful reconnect attempt ISO timestamp — for UX indicators. */
  lastConnectedAt: string | null;
  /** Manually tear down the stream (e.g. on session leave). */
  disconnect: () => void;
}

/** Parse SSE chunk buffer into SessionEvents; returns new events + leftover buffer. */
function parseSessionEventChunk(buffer: string): { events: SessionEvent[]; rest: string } {
  const events: SessionEvent[] = [];
  const parts = buffer.split('\n\n');
  const rest = parts.pop() ?? '';
  for (const part of parts) {
    const lines = part.split('\n');
    const dataLines = lines.filter((l) => l.startsWith('data:'));
    const data = dataLines.map((l) => l.slice(5).trim()).join('\n');
    if (!data) continue;
    try {
      events.push(JSON.parse(data) as SessionEvent);
    } catch {
      // Malformed frame — drop silently (stream stays healthy).
    }
  }
  return { events, rest };
}

const MAX_BACKOFF_SECONDS = 30;
const INITIAL_BACKOFF_SECONDS = 1.0;

export function useSessionEvents(options: SessionEventStreamOptions): SessionEventStreamHandle {
  const { token, sessionId, workspaceId = 'local-admin', authHeaders, eventType, pollSeconds, onEvent } = options;
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [state, setState] = useState<SessionEventStreamState>('idle');
  const [lastConnectedAt, setLastConnectedAt] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  const sessionIdRef = useRef<string | undefined>(sessionId);
  const afterIdRef = useRef<string | undefined>(undefined);
  const backoffRef = useRef<number>(INITIAL_BACKOFF_SECONDS);

  // Keep refs in sync without re-triggering effects
  useEffect(() => {
    sessionIdRef.current = sessionId;
    afterIdRef.current = undefined; // new session = reset cursor
    backoffRef.current = INITIAL_BACKOFF_SECONDS;
  }, [sessionId]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, []);

  const disconnect = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setState((s) => (s === 'streaming' || s === 'connecting' || s === 'reconnecting' ? 'closed' : s));
  }, []);

  useEffect(() => {
    // Guard: no token or no session → stay idle
    if (!token || !sessionId) {
      setState('idle');
      return;
    }

    setState('connecting');

    let cancelled = false;

    const runStream = async () => {
      const sid = sessionIdRef.current!;
      const params = new URLSearchParams({ workspaceId });
      if (eventType) params.set('eventType', eventType);
      if (afterIdRef.current) params.set('afterId', afterIdRef.current);
      if (pollSeconds) params.set('pollSeconds', String(pollSeconds));

      const url = `/api/v1/sessions/${encodeURIComponent(sid)}/events/stream?${params.toString()}`;

      // If a previous controller is still alive, kill it first
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      try {
        if (process.env.NODE_ENV === 'development') {
          // eslint-disable-next-line no-console
          console.log('[useSessionEvents] connecting', { url, afterId: afterIdRef.current });
        }

        const res = await fetch(url, {
          headers: authHeaders(),
          signal: controller.signal,
        });

        if (!res.ok || !res.body) {
          throw new Error(`SSE HTTP ${res.status}`);
        }

        // Connected — reset backoff
        backoffRef.current = INITIAL_BACKOFF_SECONDS;
        setLastConnectedAt(new Date().toISOString());
        setState('streaming');

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          if (!mountedRef.current || controller.signal.aborted) return;

          buffer += decoder.decode(value, { stream: true });
          const { events: parsed, rest } = parseSessionEventChunk(buffer);
          buffer = rest;

          for (const evt of parsed) {
            afterIdRef.current = evt.id;
            setEvents((prev) => [...prev, evt]);
            onEvent?.(evt);
          }
        }

        // Drain leftover buffer
        if (buffer.trim()) {
          const { events: parsed } = parseSessionEventChunk(buffer + '\n\n');
          for (const evt of parsed) {
            afterIdRef.current = evt.id;
            setEvents((prev) => [...prev, evt]);
            onEvent?.(evt);
          }
        }

        if (!mountedRef.current || controller.signal.aborted) return;

        // Stream ended cleanly — don't auto-reconnect unless we were in a reconnect
        setState('closed');
      } catch (err) {
        if (controller.signal.aborted || !mountedRef.current || cancelled) return;

        const msg = err instanceof Error ? err.message : String(err);

        if (process.env.NODE_ENV === 'development') {
          // eslint-disable-next-line no-console
          console.warn('[useSessionEvents] error, scheduling reconnect:', msg);
        }

        // Schedule exponential-backoff reconnect
        setState('reconnecting');
        const delay = Math.min(backoffRef.current, MAX_BACKOFF_SECONDS);
        backoffRef.current = Math.min(backoffRef.current * 2, MAX_BACKOFF_SECONDS);

        setTimeout(() => {
          if (!mountedRef.current || cancelled || controller.signal.aborted) return;
          if (sessionIdRef.current !== sid) return; // session changed while we waited
          void runStream();
        }, delay * 1000);
      }
    };

    void runStream();

    return () => {
      cancelled = true;
      abortRef.current?.abort();
      abortRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, sessionId, workspaceId, eventType, pollSeconds]);

  return { events, state, lastConnectedAt, disconnect };
}
