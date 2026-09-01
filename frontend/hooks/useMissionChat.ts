/**
 * Mission/v1 chat hook — "POST create + GET SSE stream" two-phase model.
 *
 * Replaces the legacy WebSocket surface for messages that should go
 * through Mission control plane (e.g. @mention of an Agent).
 *
 * Usage:
 *   const { sendMission, cancel, streamState, events, mentions } = useMissionChat({
 *     token, workspaceId, sessionId, authHeaders, onEvent,
 *   });
 *   await sendMission('@Architect 设计一个博客 API');
 *
 * This follows the pattern recommended in the experience recall
 * "SSE 接口对接": POST to create → GET(SSE) to subscribe, use
 * fetch + ReadableStream for the SSE side because EventSource
 * cannot send custom auth headers.
 */

import { useCallback, useRef, useState } from 'react';
import { mapMissionEvent, type ChatRenderEvent } from '../lib/missionEventMapper';
import type { MissionEvent } from '../types';

export type MissionStreamState = 'idle' | 'connecting' | 'streaming' | 'closed' | 'error';

export interface MissionMentionResult {
  resolved: Array<{ agentId: string; adapterType: string; capabilities: string[] }>;
  unresolved: Array<{ name: string; reason: string }>;
}

export interface MissionChatOptions {
  token?: string;
  workspaceId?: string;
  sessionId?: string;
  authHeaders: () => Record<string, string>;
  /** Optional callback for each mapped event (bubble, toast, etc.) */
  onEvent?: (event: ChatRenderEvent) => void;
  /** Optional callback when stream completes (success or error) */
  onComplete?: (missionId: string, state: MissionStreamState, error?: string) => void;
}

export interface MissionChatHandle {
  sendMission: (message: string) => Promise<{
    missionId: string;
    mentions: MissionMentionResult;
  } | null>;
  cancel: () => void;
  streamState: MissionStreamState;
  missionId: string | null;
  events: ChatRenderEvent[];
  mentions: MissionMentionResult | null;
}

/** Minimal SSE frame parser — "data: {...}\n\n" chunks. */
function parseSSEChunk(buffer: string): { events: MissionEvent[]; rest: string } {
  const events: MissionEvent[] = [];
  const parts = buffer.split('\n\n');
  const rest = parts.pop() ?? '';  // last chunk may be incomplete
  for (const part of parts) {
    const lines = part.split('\n');
    const dataLines = lines.filter(l => l.startsWith('data:'));
    const data = dataLines.map(l => l.slice(5).trim()).join('\n');
    if (!data) continue;
    try {
      events.push(JSON.parse(data) as MissionEvent);
    } catch {
      // Skip malformed frames — stream stays healthy.
    }
  }
  return { events, rest };
}

export function useMissionChat(options: MissionChatOptions): MissionChatHandle {
  const { authHeaders, onEvent, onComplete } = options;
  const [streamState, setStreamState] = useState<MissionStreamState>('idle');
  const [missionId, setMissionId] = useState<string | null>(null);
  const [events, setEvents] = useState<ChatRenderEvent[]>([]);
  const [mentions, setMentions] = useState<MissionMentionResult | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreamState(prev => (prev === 'streaming' || prev === 'connecting' ? 'closed' : prev));
  }, []);

  const sendMission = useCallback(async (message: string) => {
    const token = options.token;
    if (!token) {
      setStreamState('error');
      return null;
    }

    setStreamState('connecting');
    setEvents([]);
    setMentions(null);

    // ── Phase 1: POST create ──────────────────────────────────────
    let createRes: Response;
    try {
      createRes = await fetch('/api/v1/chat/mission', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({
          message,
          workspaceId: options.workspaceId ?? 'local-admin',
          sessionId: options.sessionId ?? null,
          stream: true,
        }),
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setStreamState('error');
      onComplete?.(missionId ?? '', 'error', msg);
      return null;
    }

    if (!createRes.ok) {
      const detail = await createRes.json().catch(() => ({}));
      setStreamState('error');
      onComplete?.(missionId ?? '', 'error', detail.detail ?? `HTTP ${createRes.status}`);
      return null;
    }

    const createData = await createRes.json();
    const mid = createData.missionId as string;
    const streamUrl = createData.streamUrl as string;
    const mentionRes = createData.mentions as MissionMentionResult | undefined;

    setMissionId(mid);
    setMentions(mentionRes ?? { resolved: [], unresolved: [] });

    // ── Phase 2: GET(SSE) subscribe via fetch + ReadableStream ──
    const controller = new AbortController();
    abortRef.current?.abort();
    abortRef.current = controller;

    try {
      const sseRes = await fetch(streamUrl, {
        headers: authHeaders(),
        signal: controller.signal,
      });
      if (!sseRes.ok || !sseRes.body) {
        throw new Error(`SSE HTTP ${sseRes.status}`);
      }

      setStreamState('streaming');

      const reader = sseRes.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const { events: parsed, rest } = parseSSEChunk(buffer);
        buffer = rest;

        for (const evt of parsed) {
          const rendered = mapMissionEvent(evt);
          if (rendered) {
            setEvents(prev => [...prev, rendered]);
            onEvent?.(rendered);
          }
        }
      }

      // Flush remaining buffer
      if (buffer.trim()) {
        const { events: parsed } = parseSSEChunk(buffer + '\n\n');
        for (const evt of parsed) {
          const rendered = mapMissionEvent(evt);
          if (rendered) {
            setEvents(prev => [...prev, rendered]);
            onEvent?.(rendered);
          }
        }
      }

      setStreamState('closed');
      onComplete?.(mid, 'closed');
    } catch (err) {
      if (controller.signal.aborted) {
        setStreamState('closed');
      } else {
        const msg = err instanceof Error ? err.message : String(err);
        setStreamState('error');
        onComplete?.(mid, 'error', msg);
      }
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
    }

    return { missionId: mid, mentions: mentionRes ?? { resolved: [], unresolved: [] } };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [options.token, options.workspaceId, options.sessionId, authHeaders, onEvent, onComplete]);

  return { sendMission, cancel, streamState, missionId, events, mentions };
}
