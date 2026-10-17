/**
 * Admission returns after the durable server acknowledgement.
 * SSE connection state never establishes whether a WorkUnit is executing.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { unsupportedMissionInput, type MissionChatInput } from '../lib/missionChatInput';
import type { ArchivistInfo } from '../types';
import type { ChatRenderEvent } from '../lib/missionEventMapper';
import { missionCommand, missionIdentifier, missionStreamPath, readMissionStream } from '../lib/missionStream';
import { readMissionSession, rememberMissionSession } from '../lib/missionSessionCache';

export type MissionStreamState = 'idle' | 'connecting' | 'streaming' | 'closed' | 'error';
export interface MissionMentionResult {
  resolved: Array<{ agentId: string; adapterType: string; capabilities: string[] }>;
  unresolved: Array<{ name: string; reason: string }>;
}
export interface MissionChatOptions {
  /** Pause observation without cancelling accepted work or losing this mounted view. */
  enabled?: boolean;
  /** Opt-in bounded observation window. Legacy append-only consumers retain their behavior. */
  eventLimit?: number;
  /** A mounted-only view may avoid reusing native session links after scope reset/refresh. */
  cacheSessionLink?: boolean;
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
  const { authHeaders } = options;
  const enabled = options.enabled !== false;
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;
  const callbacks = useRef(options);
  callbacks.current = options;
  const eventLimit = options.eventLimit === undefined ? undefined : Math.max(1, Math.min(500, Math.floor(options.eventLimit)));
  const key = (options.workspaceId ?? 'local-admin') + '/' + (options.sessionId ?? 'new');
  const keyRef = useRef(key);
  keyRef.current = key;
  const identity = key + '/' + (options.token ?? '');
  const identityRef = useRef(identity);
  identityRef.current = identity;
  const [streamState, setStreamState] = useState<MissionStreamState>('idle');
  const [missionId, setMissionId] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [eventBuffer, setEventBuffer] = useState<{ events: ChatRenderEvent[]; omitted: number }>({ events: [], omitted: 0 });
  const [mentions, setMentions] = useState<MissionMentionResult | null>(null);
  const [archivistInfo, setArchivistInfo] = useState<ArchivistInfo | null>(null);
  const [pending, setPending] = useState<PendingMissionConfirmation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const targetRef = useRef<string | null>(null);
  targetRef.current = pending ? 'pending/' + pending.id : missionId ? 'mission/' + missionId : null;
  const abortRef = useRef<AbortController | null>(null);
  const streamRef = useRef<{ missionId: string; url: string; identity: string } | null>(null);
  const seenRef = useRef(new Set<string>());
  const cursorRef = useRef<string | undefined>();
  const generationRef = useRef(0);
  const commands = useRef(new Set<AbortController>());
  const cancelRef = useRef<string | null>(null);
  const busyRef = useRef<string | null>(null);

  useEffect(() => {
    ++generationRef.current;
    abortRef.current?.abort();
    streamRef.current = null;
    cursorRef.current = undefined;
    seenRef.current.clear();
    busyRef.current = null;
    cancelRef.current = null;
    setMissionId(null);
    setSessionId(options.cacheSessionLink === false ? undefined : readMissionSession(key));
    setPending(null);
    setError(null);
    setEventBuffer({ events: [], omitted: 0 });
    setMentions(null);
    setArchivistInfo(null);
    setStreamState('idle');
    setSending(false);
    setCancelling(false);
    return () => {
      ++generationRef.current;
      abortRef.current?.abort();
      for (const controller of commands.current) controller.abort();
    };
  }, [identity, key, options.cacheSessionLink]);

  const subscribe = useCallback((mid: string, url: string) => {
    abortRef.current?.abort();
    const generation = ++generationRef.current;
    const controller = new AbortController();
    abortRef.current = controller;
    streamRef.current = { missionId: mid, url, identity };
    const current = () => !controller.signal.aborted && enabledRef.current
      && identityRef.current === identity && generationRef.current === generation;
    setStreamState('connecting');
    void readMissionStream(url, callbacks.current.authHeaders(), controller.signal, event => {
      if (!current()) return;
      if (event.eventId && seenRef.current.has(event.eventId)) return;
      if (event.eventId) {
        seenRef.current.add(event.eventId);
        if (missionIdentifier(event.eventId)) cursorRef.current = event.eventId;
        if (eventLimit && seenRef.current.size > eventLimit * 4) seenRef.current.delete(seenRef.current.values().next().value!);
      }
      setEventBuffer(previous => {
        if (eventLimit && event.eventId && previous.events.some(item => item.eventId === event.eventId)) return previous;
        const events = [...previous.events, event];
        let omitted = previous.omitted;
        let size = eventLimit ? events.reduce((sum, item) => sum + new TextEncoder().encode(item.content).length, 0) : 0;
        while (eventLimit && events.length && (events.length > eventLimit || size > 1_048_576)) {
          size -= new TextEncoder().encode(events.shift()!.content).length;
          ++omitted;
        }
        return { events, omitted };
      });
      callbacks.current.onEvent?.(event);
    }, () => { if (current()) setStreamState('streaming'); }, {
      missionId: mid, afterEventId: cursorRef.current, requireEventId: eventLimit !== undefined,
    }).then(() => {
      if (!current()) return;
      setStreamState('closed');
      callbacks.current.onComplete?.(mid, 'closed');
    }).catch(err => {
      if (!current()) return;
      const detail = err instanceof Error ? err.message : '事件连接失败';
      setStreamState('error');
      setError(detail);
      callbacks.current.onComplete?.(mid, 'error', detail);
    });
  }, [identity, eventLimit]);

  useEffect(() => {
    if (!enabled) {
      ++generationRef.current;
      abortRef.current?.abort();
      setStreamState('idle');
    } else {
      const stream = streamRef.current;
      if (stream?.identity === identity) subscribe(stream.missionId, stream.url);
    }
  }, [enabled, identity, subscribe]);

  const admit = useCallback((data: Record<string, any>, requestKey: string) => {
    if (!missionIdentifier(data.sessionId)) throw new Error('任务提交响应无效，请在 Mission 面板核对');
    if (data.status === 'pending') {
      if (!missionIdentifier(data.pendingId)) throw new Error('任务提交响应无效，请在 Mission 面板核对');
      if (data.ruleDescription !== undefined && typeof data.ruleDescription !== 'string') throw new Error('任务提交响应无效，请在 Mission 面板核对');
    } else {
      if (!missionIdentifier(data.missionId)) throw new Error('任务提交响应无效，请在 Mission 面板核对');
      missionStreamPath(data.streamUrl, data.missionId);
    }
    if (requestKey !== keyRef.current) return null;
    if (options.cacheSessionLink !== false) rememberMissionSession(requestKey, data.sessionId);
    abortRef.current?.abort();
    ++generationRef.current;
    seenRef.current.clear();
    cursorRef.current = undefined;
    setEventBuffer({ events: [], omitted: 0 });
    setSessionId(data.sessionId);
    if (data.status === 'pending' && typeof data.pendingId === 'string') {
      targetRef.current = 'pending/' + data.pendingId;
      setMissionId(null);
      streamRef.current = null;
      setPending({ id: data.pendingId, description: data.ruleDescription ?? '该操作需要确认' });
      setStreamState('idle');
      return { missionId: null, mentions: EMPTY_MENTIONS };
    }
    targetRef.current = 'mission/' + data.missionId;
    setMissionId(data.missionId);
    setPending(null);
    setMentions(data.mentions ?? EMPTY_MENTIONS);
    setArchivistInfo(data.archivist ?? null);
    streamRef.current = { missionId: data.missionId, url: data.streamUrl, identity };
    if (enabledRef.current) subscribe(data.missionId, data.streamUrl);
    return { missionId: data.missionId as string, mentions: (data.mentions ?? EMPTY_MENTIONS) as MissionMentionResult, archivist: data.archivist as ArchivistInfo | undefined };
  }, [identity, options.cacheSessionLink, subscribe]);

  const command = useCallback(async (path: string, body: unknown) => {
    if (!options.token || !enabledRef.current || busyRef.current === identity) return null;
    const requestKey = key;
    const requestIdentity = identity;
    busyRef.current = requestIdentity;
    setSending(true);
    setError(null);
    const controller = new AbortController();
    commands.current.add(controller);
    try {
      const response = await commandWithDeadline(path, body, authHeaders(), controller);
      if (requestIdentity !== identityRef.current) return null;
      return admit(response, requestKey);
    } catch (err) {
      if (requestIdentity === identityRef.current) {
        setError(err instanceof Error ? err.message : '任务提交失败');
        setStreamState('error');
      }
      return null;
    } finally {
      commands.current.delete(controller);
      if (busyRef.current === requestIdentity) busyRef.current = null;
      if (requestIdentity === identityRef.current) setSending(false);
    }
  }, [options.token, key, identity, authHeaders, admit]);

  const sendMission = useCallback((message: string, input: MissionChatInput = {}) => {
    const unsupported = unsupportedMissionInput(input);
    if (unsupported) {
      setError(unsupported);
      return Promise.resolve(null);
    }
    return command('/api/v1/chat/mission', {
      message, workspaceId: options.workspaceId ?? 'local-admin',
      sessionId: sessionId ?? (options.cacheSessionLink === false ? undefined : readMissionSession(key)) ?? null, stream: true,
    });
  }, [command, options.workspaceId, options.cacheSessionLink, sessionId, key]);

  const confirmPending = useCallback(() => pending ? command('/api/v1/chat/confirm', { pendingId: pending.id }) : Promise.resolve(null), [pending, command]);
  const cancel = useCallback(async () => {
    const path = pending ? '/api/v1/chat/cancel' : missionId ? '/api/v1/missions/' + encodeURIComponent(missionId) + '/cancel' : null;
    if (!path || !options.token || !enabledRef.current || cancelRef.current) return false;
    const requestIdentity = identity;
    const requestTarget = pending ? 'pending/' + pending.id : 'mission/' + missionId;
    const stillCurrent = () => requestIdentity === identityRef.current && requestTarget === targetRef.current;
    const controller = new AbortController();
    commands.current.add(controller);
    cancelRef.current = requestTarget;
    setCancelling(true);
    try {
      const acknowledgement = await commandWithDeadline(path, pending ? { pendingId: pending.id } : {}, authHeaders(), controller);
      if (!stillCurrent()) return false;
      if (acknowledgement.status !== (pending ? 'cancelled' : 'CANCELLED')) throw new Error('取消结果未确认，请重新读取任务状态');
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
    } finally {
      commands.current.delete(controller);
      if (cancelRef.current === requestTarget) cancelRef.current = null;
      if (requestIdentity === identityRef.current) setCancelling(false);
    }
  }, [pending, missionId, identity, options.token, authHeaders]);

  const reconnect = useCallback(() => {
    const stream = streamRef.current;
    if (enabledRef.current && stream?.identity === identity && stream.missionId === missionId) {
      setError(null);
      subscribe(stream.missionId, stream.url);
    }
  }, [identity, missionId, subscribe]);

  return { sendMission, cancel, confirmPending, reconnect, streamState, missionId, sessionId, events: eventBuffer.events,
    omittedEvents: eventBuffer.omitted, mentions, archivistInfo, pending, error, sending, cancelling };
}

async function commandWithDeadline(path: string, body: unknown, headers: Record<string, string>, controller: AbortController) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([missionCommand(path, body, headers, controller.signal), new Promise<never>((_, reject) => {
      timer = setTimeout(() => { controller.abort(); reject(new Error('请求结果未确认，请在 Mission 面板核对后再操作')); }, 20_000);
    })]);
  } finally { clearTimeout(timer); }
}

export type MissionChatHandle = ReturnType<typeof useMissionChat>;
