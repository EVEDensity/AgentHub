import { useCallback, useEffect } from 'react';
import { mergeReloadedMessages } from '../lib/messageRecovery';
import { replaceSessionMessages, updateSessionMessages } from '../lib/sessionStore';
import { syncDagFromMessages } from '../lib/dagStore';
import type { Message } from '../types';

export function sortMessagesByTimestamp(messages: Message[]): Message[] {
  return [...messages].sort((a, b) => a.timestamp.localeCompare(b.timestamp));
}

interface UseSessionRecoveryOptions {
  sessionId: string;
  token: string;
  messages: Message[];
  onTokenExpired: () => void;
}

export function useSessionRecovery({
  sessionId,
  token,
  messages,
  onTokenExpired,
}: UseSessionRecoveryOptions) {
  useEffect(() => {
    if (!sessionId) return;
    syncDagFromMessages(sessionId, messages);
  }, [sessionId, messages]);

  const reloadMessages = useCallback(async (merge = false): Promise<void> => {
    if (!sessionId) return;

    try {
      const headers: Record<string, string> = {};
      if (token) {
        headers.Authorization = `Bearer ${token}`;
      }
      const res = await fetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}/messages`, { headers });
      if (!res.ok) {
        if (res.status === 401) onTokenExpired();
        return;
      }
      const data: Message[] = (await res.json()) as Message[];
      if (merge) {
        updateSessionMessages(sessionId, (prev) => mergeReloadedMessages(prev, data));
      } else {
        replaceSessionMessages(sessionId, sortMessagesByTimestamp(data));
      }
      syncDagFromMessages(sessionId, data);
    } catch {
      /* ignore */
    }
  }, [sessionId, token, onTokenExpired]);

  return { reloadMessages };
}
