import { useCallback, useEffect, useRef, type MutableRefObject } from 'react';
import { registerReplayMessageId } from '../lib/messageRecovery';
import { pinSession, unpinSession } from '../lib/sessionStore';
import { buildChatWebSocketUrl } from '../lib/websocketUrl';
import type { PendingMessage } from '../types';

type WebSocketToast = {
  type: 'error' | 'warning' | 'success' | 'info';
  title: string;
  message: string;
  duration?: number;
};

export type SessionWebSocketMessageHandler = (
  raw: Record<string, unknown>,
  eventName: string | undefined,
  sessionId: string,
  ws: WebSocket,
) => void;

interface UseSessionWebSocketOptions {
  wsRef: MutableRefObject<Map<string, WebSocket>>;
  currentSessionRef: MutableRefObject<string>;
  tokenRef: MutableRefObject<string>;
  setConnected: (connected: boolean) => void;
  setNotice: (notice: string) => void;
  addToast: (toast: WebSocketToast) => void;
  onSessionClosed: (sessionId: string) => void;
  onMessage: SessionWebSocketMessageHandler;
}

export type SendQueueResult = 'sent' | 'queued';

export function computeReconnectDelay(attempt: number, jitterMs = Math.random() * 500): number {
  return Math.min(1000 * Math.pow(2, attempt), 30000) + jitterMs;
}

function useLatestRef<T>(value: T): MutableRefObject<T> {
  const ref = useRef(value);
  useEffect(() => {
    ref.current = value;
  }, [value]);
  return ref;
}

function parseSocketMessage(data: string): Record<string, unknown> | null {
  try {
    return JSON.parse(data) as Record<string, unknown>;
  } catch {
    return null;
  }
}

export function buildSocketDedupKey(eventName: string | undefined, raw: Record<string, unknown>): string {
  const messageId = String(raw.id || raw.messageId || '');
  if (!eventName || !messageId) return '';
  if (eventName === 'message_chunk') {
    const sequence = raw.sequence ?? raw.seq ?? raw.chunkIndex;
    return sequence === undefined ? '' : `${eventName}:${messageId}:${String(sequence)}`;
  }
  return `${eventName}:${messageId}`;
}

export function useSessionWebSocket({
  wsRef,
  currentSessionRef,
  tokenRef,
  setConnected,
  setNotice,
  addToast,
  onSessionClosed,
  onMessage,
}: UseSessionWebSocketOptions) {
  const wsReadyRef = useRef<Map<string, boolean>>(new Map());
  const pendingBySessionRef = useRef<Map<string, PendingMessage[]>>(new Map());
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const lastMessageIdBySessionRef = useRef<Map<string, string>>(new Map());
  const dedupIdsBySessionRef = useRef<Map<string, Set<string>>>(new Map());

  const setConnectedRef = useLatestRef(setConnected);
  const setNoticeRef = useLatestRef(setNotice);
  const addToastRef = useLatestRef(addToast);
  const onSessionClosedRef = useLatestRef(onSessionClosed);
  const onMessageRef = useLatestRef(onMessage);

  const cancelReconnect = useCallback((): void => {
    if (!reconnectTimerRef.current) return;
    clearTimeout(reconnectTimerRef.current);
    reconnectTimerRef.current = null;
  }, []);

  const isCurrentSession = useCallback(
    (sessionId: string): boolean => currentSessionRef.current === sessionId,
    [currentSessionRef],
  );

  const queuePendingMessage = useCallback((sessionId: string, message: PendingMessage): void => {
    const queued = pendingBySessionRef.current.get(sessionId) ?? [];
    const exists = queued.some((item) => item.timestamp === message.timestamp);
    if (exists) return;
    pendingBySessionRef.current.set(sessionId, [...queued, message]);
  }, []);

  const flushPendingMessages = useCallback((sessionId: string, ws: WebSocket): void => {
    const queued = pendingBySessionRef.current.get(sessionId);
    if (!queued?.length) return;

    const remaining: PendingMessage[] = [];
    for (let i = 0; i < queued.length; i += 1) {
      if (ws.readyState !== WebSocket.OPEN) {
        remaining.push(...queued.slice(i));
        break;
      }
      try {
        ws.send(JSON.stringify(queued[i]));
      } catch {
        remaining.push(...queued.slice(i));
        break;
      }
    }

    if (remaining.length > 0) {
      pendingBySessionRef.current.set(sessionId, remaining);
    } else {
      pendingBySessionRef.current.delete(sessionId);
    }
  }, []);

  const closeSession = useCallback((sessionId: string): void => {
    const existing = wsRef.current.get(sessionId);
    if (existing) {
      existing.onclose = null;
      existing.onerror = null;
      existing.onmessage = null;
      existing.onopen = null;
      try {
        existing.close();
      } catch {
        /* best effort */
      }
      wsRef.current.delete(sessionId);
    }
    wsReadyRef.current.delete(sessionId);
    pendingBySessionRef.current.delete(sessionId);
    lastMessageIdBySessionRef.current.delete(sessionId);
    dedupIdsBySessionRef.current.delete(sessionId);
    unpinSession(sessionId);
    onSessionClosedRef.current(sessionId);
    if (isCurrentSession(sessionId)) {
      cancelReconnect();
      setConnectedRef.current(false);
    }
  }, [cancelReconnect, isCurrentSession, onSessionClosedRef, setConnectedRef]);

  const connectSession = useCallback((targetSessionId?: string): void => {
    const sessionId = targetSessionId || currentSessionRef.current;
    currentSessionRef.current = sessionId;
    const token = tokenRef.current;
    if (!sessionId || !token) return;

    const existing = wsRef.current.get(sessionId);
    if (existing) {
      if (existing.readyState === WebSocket.OPEN || existing.readyState === WebSocket.CONNECTING) {
        return;
      }
      try {
        existing.close();
      } catch {
        /* ignore stale sockets */
      }
      wsRef.current.delete(sessionId);
      wsReadyRef.current.delete(sessionId);
    }

    pinSession(sessionId);
    cancelReconnect();

    const ws = new WebSocket(buildChatWebSocketUrl(sessionId, token));
    wsRef.current.set(sessionId, ws);

    ws.onopen = () => {
      if (wsRef.current.get(sessionId) !== ws) return;
      wsReadyRef.current.set(sessionId, true);
      reconnectAttemptsRef.current = 0;
      if (isCurrentSession(sessionId)) {
        setConnectedRef.current(true);
        setNoticeRef.current('WebSocket connected');
      }

      flushPendingMessages(sessionId, ws);

      const lastMessageId = lastMessageIdBySessionRef.current.get(sessionId);
      if (lastMessageId && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
          event: 'sync_request',
          lastMessageId,
        }));
      }
    };

    ws.onclose = () => {
      wsReadyRef.current.delete(sessionId);
      if (wsRef.current.get(sessionId) === ws) {
        wsRef.current.delete(sessionId);
      }
      onSessionClosedRef.current(sessionId);

      if (isCurrentSession(sessionId)) {
        setConnectedRef.current(false);
        const attempt = reconnectAttemptsRef.current;
        if (attempt === 0) {
          addToastRef.current({
            type: 'warning',
            title: 'WebSocket 连接断开',
            message: '正在尝试重新连接...',
            duration: 5000,
          });
        } else if (attempt >= 3) {
          addToastRef.current({
            type: 'error',
            title: '连接不稳定',
            message: `已尝试重连 ${attempt + 1} 次，请检查网络`,
            duration: 0,
          });
        }

        if (tokenRef.current) {
          if (reconnectAttemptsRef.current >= 10) {
            setNoticeRef.current('连接已断开，请刷新页面或重新登录');
            addToastRef.current({
              type: 'error',
              title: '连接彻底断开',
              message: '请刷新页面或重新登录',
              duration: 0,
            });
            return;
          }
          const delay = computeReconnectDelay(reconnectAttemptsRef.current);
          reconnectAttemptsRef.current += 1;
          cancelReconnect();
          reconnectTimerRef.current = setTimeout(() => {
            if (!tokenRef.current || currentSessionRef.current !== sessionId) return;
            connectSession(sessionId);
          }, delay);
        }
      }
    };

    ws.onerror = () => {
      wsReadyRef.current.delete(sessionId);
      if (isCurrentSession(sessionId)) {
        setConnectedRef.current(false);
      }
      if (ws.readyState !== WebSocket.CLOSED && ws.readyState !== WebSocket.CLOSING) {
        try {
          ws.close();
        } catch {
          /* best effort */
        }
      }
    };

    ws.onmessage = (event: MessageEvent<string>) => {
      const raw = parseSocketMessage(event.data);
      if (!raw) return;
      const evt = raw.event as string | undefined;
      const chunkSessionId = (raw.sessionId || sessionId) as string;

      if (evt === 'ping') {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ event: 'pong', ts: Date.now() }));
        }
        return;
      }

      const msgId = (raw.id || raw.messageId || '') as string;
      const dedupKey = buildSocketDedupKey(evt, raw);
      const seenIds = dedupIdsBySessionRef.current.get(chunkSessionId) ?? new Set<string>();
      if (dedupKey && seenIds.has(dedupKey)) {
        return;
      }
      if (dedupKey) {
        dedupIdsBySessionRef.current.set(
          chunkSessionId,
          registerReplayMessageId(seenIds, dedupKey),
        );
      }

      if (msgId) {
        lastMessageIdBySessionRef.current.set(chunkSessionId, msgId);
      }

      onMessageRef.current(raw, evt, chunkSessionId, ws);
    };
  }, [
    cancelReconnect,
    currentSessionRef,
    flushPendingMessages,
    isCurrentSession,
    onMessageRef,
    onSessionClosedRef,
    setConnectedRef,
    setNoticeRef,
    addToastRef,
    tokenRef,
  ]);

  const sendOrQueue = useCallback((sessionId: string, message: PendingMessage): SendQueueResult => {
    const ws = wsRef.current.get(sessionId);
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(message));
      return 'sent';
    }

    queuePendingMessage(sessionId, message);
    connectSession(sessionId);
    setNoticeRef.current('Message queued for retry');
    addToastRef.current({
      type: 'warning',
      title: '消息已排队',
      message: 'WebSocket 未连接，正在尝试重连后发送...',
      duration: 5000,
    });
    return 'queued';
  }, [addToastRef, connectSession, queuePendingMessage, setNoticeRef]);

  const disconnectAll = useCallback((): void => {
    cancelReconnect();
    const sessionIds = Array.from(wsRef.current.keys());
    sessionIds.forEach((sessionId) => closeSession(sessionId));
    wsRef.current.clear();
    wsReadyRef.current.clear();
    pendingBySessionRef.current.clear();
    lastMessageIdBySessionRef.current.clear();
    dedupIdsBySessionRef.current.clear();
    reconnectAttemptsRef.current = 0;
  }, [cancelReconnect, closeSession]);

  useEffect(() => disconnectAll, [disconnectAll]);

  return {
    wsRef,
    connectSession,
    closeSession,
    disconnectAll,
    cancelReconnect,
    sendOrQueue,
  };
}
