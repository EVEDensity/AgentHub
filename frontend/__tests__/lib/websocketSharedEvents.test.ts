import { describe, expect, it, vi, beforeEach } from 'vitest';
import { handleSharedWebSocketEvent } from '../../lib/websocketSharedEvents';
import { clearSession, getSessionStore } from '../../lib/sessionStore';
import type { ChatSession, WorkspacePreviewTab } from '../../types';

describe('websocketSharedEvents', () => {
  beforeEach(() => {
    clearSession('session-1');
  });

  it('refreshes workspace previews on workspace change', () => {
    let version = 0;
    let tabs: WorkspacePreviewTab[] = [
      { id: 'tab-1', path: '/tmp/a.ts', kind: 'file' },
    ];

    const handled = handleSharedWebSocketEvent(
      { event: 'workspace_change', path: '/tmp/a.ts' },
      'workspace_change',
      'session-1',
      {
        wsRef: { current: new Map() },
        setWorkspaceVersion: (updater) => {
          version = typeof updater === 'function' ? updater(version) : updater;
        },
        setPreviewTabs: (updater) => {
          tabs = typeof updater === 'function' ? updater(tabs) : updater;
        },
        setNotice: vi.fn(),
        setPmState: vi.fn(),
        setDegradationStatus: vi.fn(),
        setSessions: vi.fn(),
        setIsAutoNaming: vi.fn(),
        setExecPermission: vi.fn(),
        sortSessions: (items: ChatSession[]) => items,
      },
    );

    expect(handled).toBe(true);
    expect(version).toBe(1);
    expect(tabs[0]._version).toBe(1);
  });

  it('stores task preview messages in the session store', () => {
    handleSharedWebSocketEvent(
      {
        event: 'task_preview',
        sessionId: 'session-1',
        messageId: 'msg-1',
        timestamp: '2026-07-19T00:00:00.000Z',
        tasks: [
          {
            id: 'node-1',
            description: 'Inspect repo',
            agent: 'Orchestrator',
            dependencies: [],
          },
        ],
      },
      'task_preview',
      'session-1',
      {
        wsRef: { current: new Map() },
        setWorkspaceVersion: vi.fn(),
        setPreviewTabs: vi.fn(),
        setNotice: vi.fn(),
        setPmState: vi.fn(),
        setDegradationStatus: vi.fn(),
        setSessions: vi.fn(),
        setIsAutoNaming: vi.fn(),
        setExecPermission: vi.fn(),
        sortSessions: (items: ChatSession[]) => items,
      },
    );

    const messages = getSessionStore().getState('session-1').messages;
    expect(messages).toHaveLength(1);
    expect(messages[0].type).toBe('task_preview');
  });

  it('updates session names on rename', () => {
    let sessions: ChatSession[] = [{ id: 'session-1', name: 'Old' } as ChatSession];

    handleSharedWebSocketEvent(
      { event: 'session_renamed', sessionId: 'session-1', name: 'New' },
      'session_renamed',
      'session-1',
      {
        wsRef: { current: new Map() },
        setWorkspaceVersion: vi.fn(),
        setPreviewTabs: vi.fn(),
        setNotice: vi.fn(),
        setPmState: vi.fn(),
        setDegradationStatus: vi.fn(),
        setSessions: (updater) => {
          sessions = typeof updater === 'function' ? updater(sessions) : updater;
        },
        setIsAutoNaming: vi.fn(),
        setExecPermission: vi.fn(),
        sortSessions: (items: ChatSession[]) => items,
      },
    );

    expect(sessions[0].name).toBe('New');
  });
});
