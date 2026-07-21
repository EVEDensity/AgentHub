import { describe, expect, it } from 'vitest';
import {
  buildDagStateFromTaskPreview,
  clearDagSession,
  deriveDagStateFromMessages,
  getDagState,
  mergeDagTaskUpdate,
  mergeRecoveredDagState,
  selectLatestPersistedDagState,
  syncDagFromMessages,
} from '../../lib/dagStore';
import type { Message, TaskPreviewEvent } from '../../types';

describe('dagStore helpers', () => {
  it('builds a pending dag from task preview payload', () => {
    const payload: TaskPreviewEvent = {
      event: 'task_preview',
      sessionId: 'session-1',
      messageId: 'msg-1',
      timestamp: '2026-07-19T00:00:00.000Z',
      tasks: [
        {
          id: 'node-1',
          description: 'Inspect repo',
          agent: 'Orchestrator',
          dependencies: ['node-0'],
          estimatedSeconds: 90,
        },
      ],
    };

    const dag = buildDagStateFromTaskPreview(payload);

    expect(dag.total).toBe(1);
    expect(dag.completed).toBe(0);
    expect(dag.nodes[0]).toMatchObject({
      id: 'node-1',
      agent: 'Orchestrator',
      description: 'Inspect repo',
      dependencies: ['node-0'],
      status: 'PENDING',
      estimated_effort: '90s',
    });
  });

  it('merges task updates with progress metadata', () => {
    const prev = {
      total: 2,
      completed: 0,
      nodes: [
        { id: 'node-1', agent: 'Orchestrator', description: 'Plan', dependencies: [], status: 'PENDING' },
        { id: 'node-2', agent: 'CodeGen', description: 'Build', dependencies: ['node-1'], status: 'PENDING' },
      ],
    };

    const next = mergeDagTaskUpdate(prev, {
      nodeId: 'node-1',
      status: 'SUCCESS',
      detail: { error: 'ignored' },
      progress: { completed: 1, total: 2, percent: 50 },
    });

    expect(next.total).toBe(2);
    expect(next.completed).toBe(1);
    expect(next.nodes[0]).toMatchObject({ status: 'SUCCESS' });
  });

  it('derives a dag from the latest task preview message', () => {
    const messages: Message[] = [
      {
        event: 'message',
        sessionId: 'session-1',
        sender: 'system',
        content: 'older',
        type: 'text',
        timestamp: '2026-07-19T00:00:00.000Z',
      },
      {
        event: 'message',
        sessionId: 'session-1',
        sender: 'system',
        content: '任务预览',
        type: 'task_preview',
        timestamp: '2026-07-19T00:00:01.000Z',
        taskPreviewData: {
          event: 'task_preview',
          sessionId: 'session-1',
          messageId: 'msg-2',
          timestamp: '2026-07-19T00:00:01.000Z',
          tasks: [
            { id: 'node-3', description: 'Review', agent: 'Review', dependencies: [] },
          ],
        },
      },
    ];

    const dag = deriveDagStateFromMessages(messages);

    expect(dag.total).toBe(1);
    expect(dag.nodes[0].id).toBe('node-3');
  });

  it('forces dag refresh from reloaded messages', () => {
    clearDagSession('session-force-refresh');
    const initial: Message[] = [
      {
        event: 'message',
        sessionId: 'session-force-refresh',
        sender: 'system',
        content: 'task preview',
        type: 'task_preview',
        timestamp: '2026-07-19T00:00:00.000Z',
        taskPreviewData: {
          event: 'task_preview',
          sessionId: 'session-force-refresh',
          messageId: 'msg-1',
          timestamp: '2026-07-19T00:00:00.000Z',
          tasks: [{ id: 'node-old', description: 'Old', agent: 'A', dependencies: [] }],
        },
      },
    ];
    const refreshed: Message[] = [
      {
        event: 'message',
        sessionId: 'session-force-refresh',
        sender: 'system',
        content: 'task preview',
        type: 'task_preview',
        timestamp: '2026-07-19T00:00:01.000Z',
        taskPreviewData: {
          event: 'task_preview',
          sessionId: 'session-force-refresh',
          messageId: 'msg-2',
          timestamp: '2026-07-19T00:00:01.000Z',
          tasks: [{ id: 'node-new', description: 'New', agent: 'B', dependencies: [] }],
        },
      },
    ];

    syncDagFromMessages('session-force-refresh', initial);
    expect(getDagState('session-force-refresh').nodes[0].id).toBe('node-old');

    syncDagFromMessages('session-force-refresh', refreshed);
    expect(getDagState('session-force-refresh').nodes[0].id).toBe('node-old');

    syncDagFromMessages('session-force-refresh', refreshed, true);
    expect(getDagState('session-force-refresh').nodes[0].id).toBe('node-new');
  });

  it('merges a persisted snapshot without regressing newer live progress', () => {
    const snapshot = {
      total: 2,
      completed: 0,
      nodes: [
        { id: 'node-1', status: 'PENDING', description: 'Plan' },
        { id: 'node-2', status: 'PENDING', description: 'Build' },
      ],
    };
    const live = {
      total: 2,
      completed: 1,
      nodes: [
        { id: 'node-1', status: 'SUCCESS', description: 'Plan' },
        { id: 'node-2', status: 'RUNNING', description: 'Build' },
      ],
    };

    expect(mergeRecoveredDagState(snapshot, live)).toMatchObject({
      completed: 1,
      nodes: [{ status: 'SUCCESS' }, { status: 'RUNNING' }],
    });
  });

  it('selects the newest persisted task that contains a dag', () => {
    const dag = { total: 1, completed: 1, nodes: [{ id: 'done', status: 'SUCCESS' }] };
    expect(selectLatestPersistedDagState([
      {},
      { dagProgress: dag },
      { dagProgress: { total: 1, completed: 0, nodes: [{ id: 'old' }] } },
    ])).toEqual(dag);
  });
});
