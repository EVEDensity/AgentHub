/**
 * 多 Session 并发流式状态管理
 *
 * 解决：用户切到别的 session 时，旧 session 正在流的消息被前端单例状态丢掉的 bug。
 * 思路：所有 session 的 messages / streamBuffer / isStreaming 走 Map<sessionId, *>
 *       切换 session 时不清空 Map，后台的流继续吃 chunk；切回来时从 Map 拿数据，
 *       不发 API 也能恢复。后端 WebSocketManager 本身已按 sessionId 隔离推流，前端
 *       只需要把单例改成 Map 即可。
 */
import { useCallback, useSyncExternalStore } from 'react';
import type { Message } from '../types';

export interface StreamBuffer {
  messageId: string;
  sessionId: string;
  chunks: string[];
  isFinal: boolean;
}

export interface SessionState {
  messages: Message[];
  buffer: StreamBuffer | null;
  isStreaming: boolean;
}

/** LRU 上限：最多缓存 8 个 session 的完整状态。超出后最早的不活跃的先踢。 */
const MAX_CACHED_SESSIONS = 8;

class SessionStore {
  private sessions = new Map<string, SessionState>();
  /** Global listeners — notified on ANY session change (backward compat) */
  private listeners = new Set<() => void>();
  /** Per-session listeners — only notified when their specific session changes */
  private listenersBySession = new Map<string, Set<() => void>>();
  /** LRU 队列：队尾 = 最新访问，队首 = 最早访问 */
  private lru: string[] = [];
  /** 引用计数：正在被 WebSocket 推送的 session 不能被 LRU 踢出 */
  private pinned = new Set<string>();

  getState(sessionId: string): SessionState {
    let state = this.sessions.get(sessionId);
    if (!state) {
      state = { messages: [], buffer: null, isStreaming: false };
      this.sessions.set(sessionId, state);
    }
    return state;
  }

  setState(sessionId: string, updater: (prev: SessionState) => SessionState): void {
    const prev = this.getState(sessionId);
    const next = updater(prev);
    // 没有任何字段引用变了，跳过通知
    if (
      next.messages === prev.messages &&
      next.buffer === prev.buffer &&
      next.isStreaming === prev.isStreaming
    ) {
      return;
    }
    this.sessions.set(sessionId, next);
    this.touchLRU(sessionId);
    this.evictIfNeeded();
    this.notify(sessionId);
  }

  removeSession(sessionId: string): void {
    if (!this.sessions.has(sessionId)) return;
    this.sessions.delete(sessionId);
    this.lru = this.lru.filter((id) => id !== sessionId);
    this.pinned.delete(sessionId);
    this.listenersBySession.delete(sessionId);
    this.notify(sessionId);
  }

  pin(sessionId: string): void {
    this.pinned.add(sessionId);
    this.touchLRU(sessionId);
  }

  unpin(sessionId: string): void {
    this.pinned.delete(sessionId);
    this.evictIfNeeded();
    this.notify(sessionId);
  }

  private touchLRU(sessionId: string): void {
    const idx = this.lru.indexOf(sessionId);
    if (idx >= 0) this.lru.splice(idx, 1);
    this.lru.push(sessionId);
  }

  private evictIfNeeded(): void {
    let iterations = 0;
    const maxIterations = this.lru.length * 2;
    while (this.lru.length > MAX_CACHED_SESSIONS) {
      if (iterations++ > maxIterations) {
        // Safety valve: if all sessions are pinned, break to avoid infinite loop.
        // Falls back to evicting the oldest pinned session.
        console.warn(
          '[SessionStore] LRU eviction stuck — all sessions pinned. Evicting oldest pinned session.',
        );
        const fallbackId = this.lru.shift();
        if (fallbackId) {
          this.pinned.delete(fallbackId);
          this.sessions.delete(fallbackId);
        }
        continue;
      }
      const evictId = this.lru[0];
      if (this.pinned.has(evictId)) {
        // 被 pin 的不能踢，把它移到队尾重新排队
        this.lru.shift();
        this.lru.push(evictId);
        continue;
      }
      this.lru.shift();
      this.sessions.delete(evictId);
    }
  }

  subscribe(listener: () => void, sessionId?: string): () => void {
    if (sessionId) {
      let set = this.listenersBySession.get(sessionId);
      if (!set) {
        set = new Set();
        this.listenersBySession.set(sessionId, set);
      }
      set.add(listener);
      return () => {
        set?.delete(listener);
        if (set && set.size === 0) {
          this.listenersBySession.delete(sessionId);
        }
      };
    }
    // Global listener (backward compat)
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private notify(sessionId?: string): void {
    // Notify global listeners
    this.listeners.forEach((l) => l());
    // Notify per-session listeners for the specific session
    if (sessionId) {
      const set = this.listenersBySession.get(sessionId);
      if (set) set.forEach((l) => l());
    }
  }
}

/** 单例 store：模块级别，所有组件共享 */
const sessionStore = new SessionStore();
export function getSessionStore(): SessionStore {
  return sessionStore;
}

/** Stable subscribe callback — same reference across renders for useSyncExternalStore */
const storeSubscribe = (onStoreChange: () => void): (() => void) =>
  sessionStore.subscribe(onStoreChange);

/** Empty array 的稳定单例 —— server snapshot / 还没创建过 session 时复用。 */
const EMPTY_MESSAGES: Message[] = Object.freeze([]) as unknown as Message[];

/**
 * 订阅指定 session 的 messages 数组。
 * 切到别的 session 后，旧 session 的 messages 仍在 Map 里持续更新，
 * 切回来时 React 自动从 useSyncExternalStore 拿到最新值。
 *
 * Now uses per-session subscriptions — only re-subscribes when sessionId changes,
 * so streaming chunks on session A never trigger getSnapshot calls on session B.
 */
export function useSessionMessages(sessionId: string): Message[] {
  const subscribe = useCallback(
    (onStoreChange: () => void) => sessionStore.subscribe(onStoreChange, sessionId),
    [sessionId],
  );
  const getSnapshot = useCallback(
    () => sessionStore.getState(sessionId).messages,
    [sessionId],
  );
  return useSyncExternalStore(
    subscribe,
    getSnapshot,
    () => EMPTY_MESSAGES,
  );
}

/** 订阅指定 session 的 isStreaming 状态。Per-session subscription. */
export function useSessionStreaming(sessionId: string): boolean {
  const subscribe = useCallback(
    (onStoreChange: () => void) => sessionStore.subscribe(onStoreChange, sessionId),
    [sessionId],
  );
  const getSnapshot = useCallback(
    () => sessionStore.getState(sessionId).isStreaming,
    [sessionId],
  );
  return useSyncExternalStore(
    subscribe,
    getSnapshot,
    () => false,
  );
}

/**
 * 写入指定 session 的 messages。updater 收到的是当前 session 的 messages 数组。
 * 跟 setMessages(prev => ...) 一样用，但带 sessionId 路由。
 */
export function updateSessionMessages(
  sessionId: string,
  updater: (prev: Message[]) => Message[],
): void {
  sessionStore.setState(sessionId, (s) => ({ ...s, messages: updater(s.messages) }));
}

/** 直接设置 isStreaming。 */
export function setSessionStreaming(sessionId: string, isStreaming: boolean): void {
  sessionStore.setState(sessionId, (s) =>
    s.isStreaming === isStreaming ? s : { ...s, isStreaming },
  );
}

/** 获取 / 设置指定 session 的流缓冲（后台 session 的流照常累积）。 */
export function getSessionBuffer(sessionId: string): StreamBuffer | null {
  return sessionStore.getState(sessionId).buffer;
}

export function setSessionBuffer(sessionId: string, buffer: StreamBuffer | null): void {
  sessionStore.setState(sessionId, (s) => ({ ...s, buffer }));
}

/** 强制设置 messages（覆盖式：用于 reloadMessages 拉到的 DB 消息）。 */
export function replaceSessionMessages(sessionId: string, messages: Message[]): void {
  sessionStore.setState(sessionId, (s) => ({ ...s, messages }));
}

/** 清空指定 session 的所有状态。删除会话时用。 */
export function clearSession(sessionId: string): void {
  sessionStore.removeSession(sessionId);
}

/** 用于在 WebSocket 连接 / 断开时 pin/unpin session 防止 LRU 误踢。 */
export function pinSession(sessionId: string): void {
  sessionStore.pin(sessionId);
}
export function unpinSession(sessionId: string): void {
  sessionStore.unpin(sessionId);
}
