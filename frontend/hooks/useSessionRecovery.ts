import { useCallback, useEffect } from 'react';
import { mergeReloadedMessages } from '../lib/messageRecovery';
import { updateSessionMessages } from '../lib/sessionStore';
import { restoreDagState, selectLatestPersistedDagState, syncDagFromMessages, type PersistedTaskSnapshot } from '../lib/dagStore';
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
      const [res, tasksRes] = await Promise.all([
        fetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}/messages`, { headers }),
        fetch(`/api/tasks?sessionId=${encodeURIComponent(sessionId)}`, { headers }).catch(() => null),
      ]);
      if (!res.ok) {
        if (res.status === 401) onTokenExpired();
        return;
      }
      const data: Message[] = (await res.json()) as Message[];
      const ordered = sortMessagesByTimestamp(data);
      updateSessionMessages(sessionId, (prev) => (merge || prev.length > 0 ? mergeReloadedMessages(prev, ordered) : ordered));

      if (tasksRes?.ok) {
        const tasks = (await tasksRes.json()) as PersistedTaskSnapshot[];
        const snapshot = Array.isArray(tasks) ? selectLatestPersistedDagState(tasks) : null;
        if (snapshot) restoreDagState(sessionId, snapshot);
        else syncDagFromMessages(sessionId, ordered, true);
      } else {
        if (tasksRes?.status === 401) onTokenExpired();
        syncDagFromMessages(sessionId, ordered, true);
      }
    } catch {
      /* ignore */
    }
  }, [sessionId, token, onTokenExpired]);

  return { reloadMessages };
}
