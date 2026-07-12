/**
 * Lightweight per-session collaboration state store.
 *
 * Tracks real-time collaboration state that isn't presence:
 *   - Which users are currently typing
 *   - Active resource locks
 *
 * Uses ``useSyncExternalStore`` for reactive subscriptions.
 */

import { useCallback, useSyncExternalStore } from 'react';

// ── Types ───────────────────────────────────────────────────────────

export interface TypingUser {
  userId: string;
  userName: string;
  isTyping: boolean;
}

interface SessionCollabSnapshot {
  typingUsers: Map<string, TypingUser>;
  cachedArray: TypingUser[] | null; // invalidated on mutation
}

// ── Store ────────────────────────────────────────────────────────────

type Listener = () => void;

class CollaborationStore {
  private sessions = new Map<string, SessionCollabSnapshot>();
  private listeners = new Set<Listener>();

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  }

  private notify(): void {
    for (const l of this.listeners) l();
  }

  private _get(sessionId: string): SessionCollabSnapshot {
    let s = this.sessions.get(sessionId);
    if (!s) {
      s = { typingUsers: new Map(), cachedArray: null };
      this.sessions.set(sessionId, s);
    }
    return s;
  }

  /** Mark the cached snapshot as stale (call after every mutation). */
  private _invalidate(sessionId: string): void {
    const s = this.sessions.get(sessionId);
    if (s) s.cachedArray = null;
    this.notify();
  }

  // ── Typing indicators ────────────────────────────────────────

  setTyping(sessionId: string, userId: string, userName: string, isTyping: boolean): void {
    const s = this._get(sessionId);
    if (isTyping) {
      s.typingUsers.set(userId, { userId, userName, isTyping: true });
    } else {
      s.typingUsers.delete(userId);
    }
    this._invalidate(sessionId);
  }

  getTypingUsers(sessionId: string): TypingUser[] {
    const s = this._get(sessionId);
    if (s.cachedArray === null) {
      s.cachedArray = Array.from(s.typingUsers.values());
    }
    return s.cachedArray;
  }

  /** Clean up when a session is destroyed. */
  clearSession(sessionId: string): void {
    this.sessions.delete(sessionId);
    this.notify();
  }

  // ── React hooks ─────────────────────────────────────────────

  useTypingUsers(sessionId: string): TypingUser[] {
    // eslint-disable-next-line react-hooks/rules-of-hooks
    const subscribe = useCallback(
      (cb: Listener) => this.subscribe(cb),
      [],
    );
    // eslint-disable-next-line react-hooks/rules-of-hooks
    const getSnap = useCallback(
      () => this.getTypingUsers(sessionId),
      [sessionId],
    );
    // eslint-disable-next-line react-hooks/rules-of-hooks
    return useSyncExternalStore(subscribe, getSnap);
  }
}

// ── Module singleton ──────────────────────────────────────────────────

let _store: CollaborationStore | null = null;

export function getCollaborationStore(): CollaborationStore {
  if (!_store) _store = new CollaborationStore();
  return _store;
}
