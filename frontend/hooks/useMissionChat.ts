/**
 * Admission returns after the durable server acknowledgement.
 * SSE connection state never establishes whether a WorkUnit is executing.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { ArchivistInfo } from '../types';
import type { ChatRenderEvent } from '../lib/missionEventMapper';
import { missionCommand, readMissionStream } from '../lib/missionStream';
import { readMissionSession, rememberMissionSession } from '../lib/missionSessionCache';

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
  onEvent?: (event: ChatRenderEvent) => void;
  onComplete?: (missionId: string, state: MissionStreamState, error?: string) => void;
}
export interface PendingMissionConfirmation { id: string; description: string; }
const EMPTY_MENTIONS: MissionMentionResult = { resolved: [], unresolved: [] };

export function useMissionChat(options: MissionChatOptions) {
  const { authHeaders, onEvent, onComplete } = options;
  const key = (options.workspaceId ?? 'local-admin') + '/' + (options.sessionId ?? 'new');
  const keyRef = useRef(key);
  keyRef.current = key;
  const identity = key + '/' + (options.token ?? '');
  const identityRef = useRef(identity);
  identityRef.current = identity;
  const [streamState, setStreamState] = useState<MissionStreamState>('idle');
  const [missionId, setMissionId] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [events, setEvents] = useState<ChatRenderEvent[]>([]);
  const [mentions, setMentions] = useState<MissionMentionResult | null>(null);
  const [archivistInfo, setArchivistInfo] = useState<ArchivistInfo | null>(null);
  const [pending, setPending] = useState<PendingMissionConfirmation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const targetRef = useRef<string | null>(null);
  targetRef.current = pending ? 'pending/' + pending.id : missionId ? 'mission/' + missionId : null;
  const abortRef = useRef<AbortController | null>(null);
  const streamRef = useRef<{ missionId: string; url: string } | null>(null);
  const seenRef = useRef(new Set<string>());
  const busyRef = useRef<string | null>(null);

  useEffect(() => {
    abortRef.current?.abort();
    setMissionId(null);
    setSessionId(readMissionSession(key));
    setPending(null);
    setError(null);
    setEvents([]);
    setStreamState('idle');
    setSending(false);
    return () => { abortRef.current?.abort(); };
  }, [key, options.token]);

  const subscribe = useCallback((mid: string, url: string) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    streamRef.current = { missionId: mid, url };
    setStreamState('connecting');
    void readMissionStream(url, authHeaders(), controller.signal, event => {
      if (event.eventId && seenRef.current.has(event.eventId)) return;
      if (event.eventId) seenRef.current.add(event.eventId);
      setEvents(previous => [...previous, event]);
      onEvent?.(event);
    }, () => setStreamState('streaming')).then(() => {
      if (controller.signal.aborted) return;
      setStreamState('closed');
      onComplete?.(mid, 'closed');
    }).catch(err => {
      if (controller.signal.aborted) return;
      const detail = err instanceof Error ? err.message : '事件连接失败';
      setStreamState('error');
      setError(detail);
      onComplete?.(mid, 'error', detail);
    });
  }, [authHeaders, onEvent, onComplete]);

  const admit = useCallback((data: Record<string, any>, requestKey: string) => {
    if (typeof data.sessionId === 'string') rememberMissionSession(requestKey, data.sessionId);
    if (requestKey !== keyRef.current) return null;
    setSessionId(data.sessionId);
    if (data.status === 'pending' && typeof data.pendingId === 'string') {
      targetRef.current = 'pending/' + data.pendingId;
      setPending({ id: data.pendingId, description: data.ruleDescription ?? '该操作需要确认' });
      setStreamState('idle');
      return { missionId: null, mentions: EMPTY_MENTIONS };
    }
    if (typeof data.missionId !== 'string' || typeof data.streamUrl !== 'string') throw new Error('任务提交响应无效');
    targetRef.current = 'mission/' + data.missionId;
    setMissionId(data.missionId);
    setPending(null);
    setMentions(data.mentions ?? EMPTY_MENTIONS);
    setArchivistInfo(data.archivist ?? null);
    seenRef.current.clear();
    subscribe(data.missionId, data.streamUrl);
    return { missionId: data.missionId as string, mentions: (data.mentions ?? EMPTY_MENTIONS) as MissionMentionResult, archivist: data.archivist as ArchivistInfo | undefined };
  }, [subscribe]);

  const command = useCallback(async (path: string, body: unknown) => {
    if (!options.token || busyRef.current === identity) return null;
    const requestKey = key;
    const requestIdentity = identity;
    busyRef.current = requestIdentity;
    setSending(true);
    setError(null);
    setEvents([]);
    try {
      const response = await missionCommand(path, body, authHeaders());
      if (requestIdentity !== identityRef.current) return null;
      return admit(response, requestKey);
    } catch (err) {
      if (requestIdentity === identityRef.current) {
        setError(err instanceof Error ? err.message : '任务提交失败');
        setStreamState('error');
      }
      return null;
    } finally {
      if (busyRef.current === requestIdentity) busyRef.current = null;
      if (requestIdentity === identityRef.current) setSending(false);
    }
  }, [options.token, key, identity, authHeaders, admit]);

  const sendMission = useCallback((message: string) => command('/api/v1/chat/mission', {
    message, workspaceId: options.workspaceId ?? 'local-admin',
    sessionId: sessionId ?? readMissionSession(key) ?? null, stream: true,
  }), [command, options.workspaceId, sessionId, key]);

  const confirmPending = useCallback(() => pending ? command('/api/v1/chat/confirm', { pendingId: pending.id }) : Promise.resolve(null), [pending, command]);
  const cancel = useCallback(async () => {
    const path = pending ? '/api/v1/chat/cancel' : missionId ? '/api/v1/missions/' + encodeURIComponent(missionId) + '/cancel' : null;
    if (!path) return false;
    const requestIdentity = identity;
    const requestTarget = pending ? 'pending/' + pending.id : 'mission/' + missionId;
    const stillCurrent = () => requestIdentity === identityRef.current && requestTarget === targetRef.current;
    try {
      await missionCommand(path, pending ? { pendingId: pending.id } : {}, authHeaders());
      if (!stillCurrent()) return false;
      abortRef.current?.abort();
      targetRef.current = missionId ? 'mission/' + missionId : null;
      setPending(null);
      setStreamState('closed');
      setError(null);
      return true;
    } catch (err) {
      if (!stillCurrent()) return false;
      setError(err instanceof Error ? err.message : '取消失败');
      return false;
    }
  }, [pending, missionId, identity, authHeaders]);

  const reconnect = useCallback(() => {
    const stream = streamRef.current;
    if (stream && stream.missionId === missionId) {
      setError(null);
      subscribe(stream.missionId, stream.url);
    }
  }, [missionId, subscribe]);

  return { sendMission, cancel, confirmPending, reconnect, streamState, missionId, sessionId, events, mentions, archivistInfo, pending, error, sending };
}

export type MissionChatHandle = ReturnType<typeof useMissionChat>;
