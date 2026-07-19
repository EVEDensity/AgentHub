import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import DagModal from '../../../components/chat/DagModal';
import { clearDagSession, restoreDagState, setDagState, useDagState } from '../../../lib/dagStore';
import { handleSharedWebSocketEvent } from '../../../lib/websocketSharedEvents';
import type { ChatSession } from '../../../types';

function buildDeps() {
  return {
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
  };
}

function DagFeed({ sessionId }: { sessionId: string }) {
  const dag = useDagState(sessionId);
  return <DagModal dag={dag} onClose={vi.fn()} />;
}

describe('dag replay UI', () => {
  beforeEach(() => {
    clearDagSession('dag-session-a');
    clearDagSession('dag-session-b');
  });

  it('keeps repeated task_update events from duplicating nodes', async () => {
    const deps = buildDeps();
    await act(async () => {
      handleSharedWebSocketEvent({
        event: 'task_preview',
        sessionId: 'dag-session-a',
        messageId: 'preview-1',
        timestamp: '2026-07-19T00:00:00.000Z',
        tasks: [
          { id: 'node-1', description: 'Plan', agent: 'Architect', dependencies: [] },
          { id: 'node-2', description: 'Build', agent: 'CodeGen', dependencies: ['node-1'] },
        ],
      }, 'task_preview', 'dag-session-a', deps);
      handleSharedWebSocketEvent({
        event: 'task_update',
        nodeId: 'node-1',
        status: 'RUNNING',
      }, 'task_update', 'dag-session-a', deps);
      handleSharedWebSocketEvent({
        event: 'task_update',
        nodeId: 'node-1',
        status: 'RUNNING',
      }, 'task_update', 'dag-session-a', deps);
    });

    render(<DagFeed sessionId="dag-session-a" />);

    expect(screen.getAllByText('Plan')).toHaveLength(1);
    expect(screen.getByText('运行中')).toBeInTheDocument();
  });

  it('preserves dag progress after remounting the same session', async () => {
    const deps = buildDeps();
    await act(async () => {
      handleSharedWebSocketEvent({
        event: 'task_preview',
        sessionId: 'dag-session-b',
        messageId: 'preview-2',
        timestamp: '2026-07-19T00:00:00.000Z',
        tasks: [
          { id: 'node-1', description: 'Plan', agent: 'Architect', dependencies: [] },
          { id: 'node-2', description: 'Build', agent: 'CodeGen', dependencies: ['node-1'] },
        ],
      }, 'task_preview', 'dag-session-b', deps);
      handleSharedWebSocketEvent({
        event: 'task_update',
        nodeId: 'node-1',
        status: 'SUCCESS',
      }, 'task_update', 'dag-session-b', deps);
    });

    const first = render(<DagFeed sessionId="dag-session-b" />);
    expect(screen.getByText('成功')).toBeInTheDocument();
    expect(screen.getByText('1/2 节点完成')).toBeInTheDocument();
    first.unmount();

    render(<DagFeed sessionId="dag-session-b" />);
    expect(screen.getByText('成功')).toBeInTheDocument();
    expect(screen.getByText('1/2 节点完成')).toBeInTheDocument();
  });

  it('does not leak dag state across sessions', async () => {
    const deps = buildDeps();
    await act(async () => {
      handleSharedWebSocketEvent({
        event: 'task_preview',
        sessionId: 'dag-session-a',
        messageId: 'preview-a',
        timestamp: '2026-07-19T00:00:00.000Z',
        tasks: [
          { id: 'node-a', description: 'Session A', agent: 'Architect', dependencies: [] },
        ],
      }, 'task_preview', 'dag-session-a', deps);
      handleSharedWebSocketEvent({
        event: 'task_preview',
        sessionId: 'dag-session-b',
        messageId: 'preview-b',
        timestamp: '2026-07-19T00:00:01.000Z',
        tasks: [
          { id: 'node-b', description: 'Session B', agent: 'CodeGen', dependencies: [] },
        ],
      }, 'task_preview', 'dag-session-b', deps);
    });

    const { rerender } = render(<DagFeed sessionId="dag-session-a" />);
    expect(screen.getByText('Session A')).toBeInTheDocument();
    expect(screen.queryByText('Session B')).not.toBeInTheDocument();

    rerender(<DagFeed sessionId="dag-session-b" />);
    expect(screen.getByText('Session B')).toBeInTheDocument();
    expect(screen.queryByText('Session A')).not.toBeInTheDocument();
  });

  it('keeps live progress when a slower recovery snapshot arrives', async () => {
    setDagState('dag-session-a', {
      total: 2,
      completed: 1,
      nodes: [
        { id: 'node-1', description: 'Recovered plan', status: 'SUCCESS' },
        { id: 'node-2', description: 'Recovered build', status: 'RUNNING' },
      ],
    });
    restoreDagState('dag-session-a', {
      total: 2,
      completed: 0,
      nodes: [
        { id: 'node-1', description: 'Recovered plan', status: 'PENDING' },
        { id: 'node-2', description: 'Recovered build', status: 'PENDING' },
      ],
    });

    render(<DagFeed sessionId="dag-session-a" />);

    expect(screen.getByText('Recovered plan')).toBeInTheDocument();
    expect(screen.getByText('Recovered build')).toBeInTheDocument();
    expect(screen.getByText('1/2 \u8282\u70b9\u5b8c\u6210')).toBeInTheDocument();
    expect(screen.getByText('\u8fd0\u884c\u4e2d')).toBeInTheDocument();
  });
});
