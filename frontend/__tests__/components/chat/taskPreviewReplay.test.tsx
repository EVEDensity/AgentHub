import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { useSessionMessages, clearSession } from '../../../lib/sessionStore';
import { handleSharedWebSocketEvent } from '../../../lib/websocketSharedEvents';
import TaskPreviewCard from '../../../components/chat/TaskPreviewCard';
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

function TaskPreviewFeed({ sessionId }: { sessionId: string }) {
  const messages = useSessionMessages(sessionId);
  const preview = messages.find((message) => message.type === 'task_preview' && message.taskPreviewData);
  if (!preview?.taskPreviewData) return null;
  return (
    <TaskPreviewCard
      data={preview.taskPreviewData}
      onSendEvent={vi.fn()}
    />
  );
}

describe('task preview replay UI', () => {
  beforeEach(() => {
    clearSession('session-a');
    clearSession('session-b');
  });

  it('deduplicates repeated task_preview events in the rendered preview UI', async () => {
    const deps = buildDeps();
    const payload = {
      event: 'task_preview',
      sessionId: 'session-a',
      messageId: 'preview-1',
      timestamp: '2026-07-19T00:00:00.000Z',
      tasks: [
        { id: 'node-1', description: 'Inspect repo', agent: 'Architect', dependencies: [] },
      ],
    };

    await act(async () => {
      handleSharedWebSocketEvent(payload, 'task_preview', 'session-a', deps);
      handleSharedWebSocketEvent(payload, 'task_preview', 'session-a', deps);
    });

    render(<TaskPreviewFeed sessionId="session-a" />);

    expect(screen.getAllByText('Inspect repo')).toHaveLength(1);
    expect(screen.getByText('共 1 个子任务')).toBeInTheDocument();
  });

  it('keeps task preview state isolated when switching sessions', async () => {
    const deps = buildDeps();
    await act(async () => {
      handleSharedWebSocketEvent({
        event: 'task_preview',
        sessionId: 'session-a',
        messageId: 'preview-a',
        timestamp: '2026-07-19T00:00:00.000Z',
        tasks: [
          { id: 'node-a', description: 'Plan A', agent: 'Architect', dependencies: [] },
        ],
      }, 'task_preview', 'session-a', deps);
      handleSharedWebSocketEvent({
        event: 'task_preview',
        sessionId: 'session-b',
        messageId: 'preview-b',
        timestamp: '2026-07-19T00:00:01.000Z',
        tasks: [
          { id: 'node-b', description: 'Plan B', agent: 'CodeGen', dependencies: [] },
        ],
      }, 'task_preview', 'session-b', deps);
    });

    const { rerender } = render(<TaskPreviewFeed sessionId="session-a" />);
    expect(screen.getByText('Plan A')).toBeInTheDocument();
    expect(screen.queryByText('Plan B')).not.toBeInTheDocument();

    rerender(<TaskPreviewFeed sessionId="session-b" />);
    expect(screen.getByText('Plan B')).toBeInTheDocument();
    expect(screen.queryByText('Plan A')).not.toBeInTheDocument();
  });
});
