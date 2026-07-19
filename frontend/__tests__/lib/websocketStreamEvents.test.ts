import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Dispatch, SetStateAction } from 'react';
import { handleStreamWebSocketEvent, type StreamPhase } from '../../lib/websocketStreamEvents';
import { clearSession, getSessionStore } from '../../lib/sessionStore';

describe('websocketStreamEvents', () => {
  beforeEach(() => {
    clearSession('session-1');
    vi.restoreAllMocks();
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb: FrameRequestCallback) => {
      cb(0);
      return 1;
    });
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
  });

  it('flushes a streamed chunk into session messages', () => {
    let phase: StreamPhase = 'idle';
    const setPhase: Dispatch<SetStateAction<StreamPhase>> = (next) => {
      phase = typeof next === 'function' ? next(phase) : next;
    };
    const handled = handleStreamWebSocketEvent(
      {
        event: 'message_chunk',
        sessionId: 'session-1',
        messageId: 'msg-1',
        content: 'hello',
        isFinal: true,
      },
      'message_chunk',
      'session-1',
      {
        streamFlushRafRef: { current: new Map() },
        progressiveFlushTimersRef: { current: new Map() },
        streamInterruptedAtRef: { current: new Map() },
        setStreamPhase: setPhase,
        setActiveTools: vi.fn(),
        setCurrentAgentName: vi.fn(),
        setSessions: vi.fn(),
        sortSessions: (sessions) => sessions,
        addToast: vi.fn(),
        setGenerated: vi.fn(),
        handleOpenFilePreview: vi.fn(),
        handleOpenDiffPreview: vi.fn(),
      },
    );

    const state = getSessionStore().getState('session-1');

    expect(handled).toBe(true);
    expect(phase).toBe('generating');
    expect(state.buffer).toBeNull();
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0].content).toBe('hello');
    expect(state.messages[0].isStreaming).toBe(false);
  });

  it('turns tool calls into tool results and opens previews', () => {
    let activeTools = ['read_file'];
    const setActiveTools: Dispatch<SetStateAction<string[]>> = (next) => {
      activeTools = typeof next === 'function' ? next(activeTools) : next;
    };
    const openPreview = vi.fn();
    const handledCall = handleStreamWebSocketEvent(
      {
        event: 'tool_call',
        sessionId: 'session-1',
        messageId: 'tool-1',
        timestamp: '2026-07-19T00:00:00.000Z',
        toolCalls: [{ name: 'read_file', arguments: {}, status: 'calling' }],
      },
      'tool_call',
      'session-1',
      {
        streamFlushRafRef: { current: new Map() },
        progressiveFlushTimersRef: { current: new Map() },
        streamInterruptedAtRef: { current: new Map() },
        setStreamPhase: vi.fn(),
        setActiveTools,
        setCurrentAgentName: vi.fn(),
        setSessions: vi.fn(),
        sortSessions: (sessions) => sessions,
        addToast: vi.fn(),
        setGenerated: vi.fn(),
        handleOpenFilePreview: openPreview,
        handleOpenDiffPreview: vi.fn(),
      },
    );

    const handledResult = handleStreamWebSocketEvent(
      {
        event: 'tool_result',
        sessionId: 'session-1',
        messageId: 'tool-1',
        timestamp: '2026-07-19T00:00:01.000Z',
        results: [
          {
            tool_name: 'read_file',
            success: true,
            result: { path: 'src/app.py', content: 'print("ok")' },
          },
        ],
      },
      'tool_result',
      'session-1',
      {
        streamFlushRafRef: { current: new Map() },
        progressiveFlushTimersRef: { current: new Map() },
        streamInterruptedAtRef: { current: new Map() },
        setStreamPhase: vi.fn(),
        setActiveTools,
        setCurrentAgentName: vi.fn(),
        setSessions: vi.fn(),
        sortSessions: (sessions) => sessions,
        addToast: vi.fn(),
        setGenerated: vi.fn(),
        handleOpenFilePreview: openPreview,
        handleOpenDiffPreview: vi.fn(),
      },
    );

    const state = getSessionStore().getState('session-1');

    expect(handledCall).toBe(true);
    expect(handledResult).toBe(true);
    expect(state.messages.some((message) => message.type === 'tool_result')).toBe(true);
    expect(activeTools).toEqual([]);
    expect(openPreview).toHaveBeenCalledWith('src/app.py', 'print("ok")', undefined, undefined);
  });
});
