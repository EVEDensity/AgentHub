/**
 * 多 Session 并发流式状态管理
 *
 * 解决：用户切到别的 session 时，旧 session 正在流的消息被前端单例状态丢掉的 bug。
 * 思路：所有 session 的 messages / streamBuffer / isStreaming 走 Map<sessionId, *>
 *       切换 session 时不清空 Map，后台的流继续吃 chunk；切回来时从 Map 拿数据，
 *       不发 API 也能恢复。后端 WebSocketManager 本身已按 sessionId 隔离推流，前端
 *       只需要把单例改成 Map 即可。
 */
import { useSyncExternalStore } from 'react';
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
  private listeners = new Set<() => void>();
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
    this.notify();
  }

  removeSession(sessionId: string): void {
    if (!this.sessions.has(sessionId)) return;
    this.sessions.delete(sessionId);
    this.lru = this.lru.filter((id) => id !== sessionId);
    this.pinned.delete(sessionId);
    this.notify();
  }

  pin(sessionId: string): void {
    this.pinned.add(sessionId);
    this.touchLRU(sessionId);
  }

  unpin(sessionId: string): void {
    this.pinned.delete(sessionId);
    this.evictIfNeeded();
    this.notify();
  }

  private touchLRU(sessionId: string): void {
    const idx = this.lru.indexOf(sessionId);
    if (idx >= 0) this.lru.splice(idx, 1);
    this.lru.push(sessionId);
  }

  private evictIfNeeded(): void {
    while (this.lru.length > MAX_CACHED_SESSIONS) {
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

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private notify(): void {
    this.listeners.forEach((l) => l());
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
 * 第三个参数 getServerSnapshot 是 Next.js Pages Router SSR 必需的：
 * 不传的话 React 18 会在服务端渲染时抛 "useSyncExternalStore requires
 * getServerSnapshot" 错误，导致整个页面挂掉。
 */
export function useSessionMessages(sessionId: string): Message[] {
  return useSyncExternalStore(
    storeSubscribe,
    () => sessionStore.getState(sessionId).messages,
    () => EMPTY_MESSAGES,
  );
}

/** 订阅指定 session 的 isStreaming 状态。 */
export function useSessionStreaming(sessionId: string): boolean {
  return useSyncExternalStore(
    storeSubscribe,
    () => sessionStore.getState(sessionId).isStreaming,
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
