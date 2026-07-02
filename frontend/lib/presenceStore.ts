/**
 * Lightweight per-session user presence store.
 *
 * Tracks which users are online in each session along with their
 * status (online / idle / typing / offline).  Uses the same
 * ``useSyncExternalStore`` pattern as ``SessionStore`` so components
 * subscribe granularly without Redux/Zustand.
 */

import { useCallback, useSyncExternalStore } from 'react';

// ── Types ───────────────────────────────────────────────────────────

export interface PresenceUser {
  userId: string;
  name: string;
  role: string;
  status: 'online' | 'idle' | 'typing' | 'offline';
}

interface SessionPresence {
  users: Map<string, PresenceUser>;  // userId → user info
}

// ── Store ────────────────────────────────────────────────────────────

type Listener = () => void;

/** Cached snapshots per session — cleared on data change so
 *  useSyncExternalStore receives stable references. */
interface SessionPresenceSnapshot {
  users: Map<string, PresenceUser>;
  cachedArray: PresenceUser[] | null; // invalidated on mutation
}

class PresenceStore {
  private sessions = new Map<string, SessionPresenceSnapshot>();
  private listeners = new Set<Listener>();

  // ── Subscribe pattern (useSyncExternalStore) ─────────────────

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  }

  private notify(): void {
    for (const l of this.listeners) l();
  }

  // ── Session-scoped access ───────────────────────────────────

  private _get(sessionId: string): SessionPresenceSnapshot {
    let s = this.sessions.get(sessionId);
    if (!s) {
      s = { users: new Map(), cachedArray: null };
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

  getUsers(sessionId: string): PresenceUser[] {
    const s = this._get(sessionId);
    if (s.cachedArray === null) {
      s.cachedArray = Array.from(s.users.values());
    }
    return s.cachedArray;
  }

  /** Replace the entire roster (e.g. on initial user_roster event). */
  setRoster(sessionId: string, users: PresenceUser[]): void {
    const s = this._get(sessionId);
    s.users.clear();
    for (const u of users) {
      s.users.set(u.userId, u);
    }
    this._invalidate(sessionId);
  }

  /** A user joined the session. */
  addUser(sessionId: string, user: PresenceUser): void {
    const s = this._get(sessionId);
    s.users.set(user.userId, user);
    this._invalidate(sessionId);
  }

  /** A user left the session. */
  removeUser(sessionId: string, userId: string): void {
    const s = this.sessions.get(sessionId);
    if (!s) return;
    s.users.delete(userId);
    this._invalidate(sessionId);
  }

  /** Update a single user's status (online → idle → typing, etc.). */
  updateStatus(sessionId: string, userId: string, status: string): void {
    const s = this.sessions.get(sessionId);
    if (!s) return;
    const u = s.users.get(userId);
    if (u) {
      u.status = status as PresenceUser['status'];
      this._invalidate(sessionId);
    }
  }

  /** Bulk status update from presence_update event. */
  bulkUpdateStatus(sessionId: string, updates: Array<{ userId: string; status: string }>): void {
    const s = this.sessions.get(sessionId);
    if (!s) return;
    for (const { userId, status } of updates) {
      const u = s.users.get(userId);
      if (u) u.status = status as PresenceUser['status'];
    }
    this._invalidate(sessionId);
  }

  /** Clean up when a session is destroyed. */
  clearSession(sessionId: string): void {
    this.sessions.delete(sessionId);
    this.notify();
  }

  /** Get a snapshot of the users array for useSyncExternalStore. */
  private getSnapshot(sessionId: string): PresenceUser[] {
    return this.getUsers(sessionId);
  }

  // ── React hooks ─────────────────────────────────────────────

  useUsers(sessionId: string): PresenceUser[] {
    // eslint-disable-next-line react-hooks/rules-of-hooks
    const subscribe = useCallback(
      (cb: Listener) => this.subscribe(cb),
      [],
    );
    // eslint-disable-next-line react-hooks/rules-of-hooks
    const getSnap = useCallback(
      () => this.getSnapshot(sessionId),
      [sessionId],
    );
    // eslint-disable-next-line react-hooks/rules-of-hooks
    return useSyncExternalStore(subscribe, getSnap);
  }
}

// ── Module singleton ──────────────────────────────────────────────────

let _store: PresenceStore | null = null;

export function getPresenceStore(): PresenceStore {
  if (!_store) _store = new PresenceStore();
  return _store;
}
