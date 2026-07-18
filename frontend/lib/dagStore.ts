import { useCallback, useSyncExternalStore } from 'react';
import type { DagState, Message, TaskPreviewEvent } from '../types';

export interface DagTaskUpdateEvent {
  nodeId: string;
  status: string;
  detail?: { error?: string };
  progress?: {
    completed?: number;
    total?: number;
    failed?: number;
    running?: number;
    percent?: number;
  };
  durationMs?: number;
  retries?: number;
}

type Listener = () => void;

const EMPTY_DAG_STATE: DagState = Object.freeze({
  total: 0,
  completed: 0,
  nodes: [],
}) as DagState;

function createEmptyDagState(): DagState {
  return EMPTY_DAG_STATE;
}

export function buildDagStateFromTaskPreview(payload: TaskPreviewEvent): DagState {
  return {
    total: payload.tasks.length,
    completed: 0,
    nodes: payload.tasks.map((task) => ({
      id: task.id,
      agent: task.agent,
      description: task.description,
      dependencies: task.dependencies,
      status: 'PENDING',
      estimated_effort: task.estimatedSeconds != null ? `${task.estimatedSeconds}s` : undefined,
    })),
  };
}

export function mergeDagTaskUpdate(prev: DagState, update: DagTaskUpdateEvent): DagState {
  if (!update.nodeId || !update.status) return prev;

  let matched = false;
  const nodes = prev.nodes.map((node) => {
    if (node.id !== update.nodeId) return node;
    matched = true;
    return {
      ...node,
      status: update.status,
      error: update.detail?.error ?? node.error,
      duration_ms: update.durationMs ?? node.duration_ms,
    };
  });

  const completed = update.progress?.completed ?? nodes.filter((node) => node.status === 'SUCCESS').length;
  const total = update.progress?.total ?? prev.total;

  if (!matched && !update.progress) {
    return prev;
  }

  return {
    ...prev,
    total,
    completed,
    nodes,
  };
}

export function deriveDagStateFromMessages(messages: Message[]): DagState {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const msg = messages[i];
    if (msg.type === 'task_preview' && msg.taskPreviewData) {
      return buildDagStateFromTaskPreview(msg.taskPreviewData);
    }
  }
  return createEmptyDagState();
}

class DagStore {
  private sessions = new Map<string, DagState>();
  private listenersBySession = new Map<string, Set<Listener>>();

  private notify(sessionId: string): void {
    const listeners = this.listenersBySession.get(sessionId);
    if (!listeners) return;
    for (const listener of listeners) listener();
  }

  private subscribe(sessionId: string, listener: Listener): () => void {
    let listeners = this.listenersBySession.get(sessionId);
    if (!listeners) {
      listeners = new Set();
      this.listenersBySession.set(sessionId, listeners);
    }
    listeners.add(listener);
    return () => {
      listeners?.delete(listener);
      if (listeners && listeners.size === 0) {
        this.listenersBySession.delete(sessionId);
      }
    };
  }

  getState(sessionId: string): DagState {
    return this.sessions.get(sessionId) ?? createEmptyDagState();
  }

  setState(sessionId: string, next: DagState): void {
    this.sessions.set(sessionId, next);
    this.notify(sessionId);
  }

  updateTask(sessionId: string, update: DagTaskUpdateEvent): void {
    const prev = this.sessions.get(sessionId) ?? createEmptyDagState();
    const next = mergeDagTaskUpdate(prev, update);
    if (next === prev) return;
    this.sessions.set(sessionId, next);
    this.notify(sessionId);
  }

  syncFromMessages(sessionId: string, messages: Message[]): void {
    if (this.sessions.has(sessionId)) return;
    const derived = deriveDagStateFromMessages(messages);
    if (derived.total === 0 && derived.nodes.length === 0) return;
    this.sessions.set(sessionId, derived);
    this.notify(sessionId);
  }

  clearSession(sessionId: string): void {
    if (!this.sessions.has(sessionId)) return;
    this.sessions.delete(sessionId);
    this.notify(sessionId);
  }

  useDagState(sessionId: string): DagState {
    // eslint-disable-next-line react-hooks/rules-of-hooks
    const subscribe = useCallback((cb: Listener) => this.subscribe(sessionId, cb), [sessionId]);
    // eslint-disable-next-line react-hooks/rules-of-hooks
    const getSnapshot = useCallback(() => this.getState(sessionId), [sessionId]);
    // eslint-disable-next-line react-hooks/rules-of-hooks
    return useSyncExternalStore(subscribe, getSnapshot, createEmptyDagState);
  }
}

const dagStore = new DagStore();

export function useDagState(sessionId: string): DagState {
  return dagStore.useDagState(sessionId);
}

export function setDagState(sessionId: string, dag: DagState): void {
  dagStore.setState(sessionId, dag);
}

export function updateDagState(sessionId: string, update: DagTaskUpdateEvent): void {
  dagStore.updateTask(sessionId, update);
}

export function syncDagFromMessages(sessionId: string, messages: Message[]): void {
  dagStore.syncFromMessages(sessionId, messages);
}

export function clearDagSession(sessionId: string): void {
  dagStore.clearSession(sessionId);
}
