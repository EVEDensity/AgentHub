import type { Dispatch, MutableRefObject, SetStateAction } from 'react';
import type {
  ChatSession,
  GeneratedData,
  Message,
  StreamChunk,
  ToolCallEvent,
  ToolResultEvent,
} from '../types';
import {
  getSessionBuffer,
  setSessionBuffer,
  setSessionStreaming,
  updateSessionMessages,
  type StreamBuffer,
} from './sessionStore';
import { mergeFinalMessage } from './messageRecovery';

export type StreamPhase = 'idle' | 'thinking' | 'executing' | 'generating' | 'done';

interface StreamToast {
  type: 'error' | 'warning' | 'success' | 'info';
  title: string;
  message: string;
  duration?: number;
}

export interface StreamWebSocketEventDeps {
  streamFlushRafRef: MutableRefObject<Map<string, number>>;
  progressiveFlushTimersRef: MutableRefObject<Map<string, number>>;
  streamInterruptedAtRef: MutableRefObject<Map<string, number>>;
  setStreamPhase: Dispatch<SetStateAction<StreamPhase>>;
  setActiveTools: Dispatch<SetStateAction<string[]>>;
  setCurrentAgentName: Dispatch<SetStateAction<string>>;
  setSessions: Dispatch<SetStateAction<ChatSession[]>>;
  sortSessions: (sessions: ChatSession[]) => ChatSession[];
  addToast: (toast: StreamToast) => void;
  setGenerated: Dispatch<SetStateAction<GeneratedData | null>>;
  handleOpenFilePreview: (path: string, content: string, language?: string, status?: string) => void;
  handleOpenDiffPreview: (path: string, diff: string) => void;
}

function isThinkingPlaceholder(message: Message): boolean {
  return !!(message.isStreaming && message.sender !== 'user' && !message.diffFilePath && message.type === 'text');
}

function preserveLatestThinkingPlaceholder(prev: Message[]): Message[] {
  const lastThinkingIdx = (() => {
    for (let i = prev.length - 1; i >= 0; i -= 1) {
      if (isThinkingPlaceholder(prev[i])) return i;
    }
    return -1;
  })();

  if (lastThinkingIdx < 0) {
    const cleaned = prev.filter((message) => !isThinkingPlaceholder(message));
    return cleaned.length !== prev.length ? cleaned : prev;
  }

  const updated = [...prev];
  if (!updated[lastThinkingIdx].content || updated[lastThinkingIdx].content.startsWith('🔧')) {
    updated[lastThinkingIdx] = {
      ...updated[lastThinkingIdx],
      content: '工具执行完成，正在综合结果生成回复...',
    };
  }
  return updated.filter((message, index) => index === lastThinkingIdx || !isThinkingPlaceholder(message));
}

export function flushStreamBuffer(
  sessionId: string,
  deps: Pick<StreamWebSocketEventDeps, 'streamFlushRafRef' | 'progressiveFlushTimersRef'>,
): void {
  const buf = getSessionBuffer(sessionId);
  if (!buf) return;

  if (buf.chunks.length === 0) {
    if (buf.isFinal) {
      setSessionStreaming(buf.sessionId, false);
      setSessionBuffer(buf.sessionId, null);
    }
    return;
  }

  const chunk = buf.chunks[0];
  const remaining = buf.chunks.slice(1);
  const isLastChunk = remaining.length === 0 && buf.isFinal;

  setSessionBuffer(buf.sessionId, {
    messageId: buf.messageId,
    sessionId: buf.sessionId,
    chunks: remaining,
    isFinal: isLastChunk ? false : buf.isFinal,
  });

  const bufMessageId = buf.messageId;
  updateSessionMessages(buf.sessionId, (prev) => {
    const idx = prev.findIndex((message) => message.messageId === bufMessageId);
    if (idx >= 0) {
      const updated = [...prev];
      updated[idx] = {
        ...updated[idx],
        content: updated[idx].content + chunk,
        isStreaming: !isLastChunk,
      };
      return updated;
    }
    const newMsg: Message = {
      event: 'message',
      sessionId: buf.sessionId,
      sender: 'agent',
      content: chunk,
      type: 'text',
      timestamp: new Date().toISOString(),
      messageId: bufMessageId,
      isStreaming: !isLastChunk,
    };
    return [...prev, newMsg];
  });

  if (isLastChunk) {
    setSessionStreaming(buf.sessionId, false);
    setSessionBuffer(buf.sessionId, null);
    return;
  }

  if (remaining.length > 0) {
    const timerId = window.setTimeout(() => {
      deps.progressiveFlushTimersRef.current.delete(sessionId);
      flushStreamBuffer(sessionId, deps);
    }, 8);
    deps.progressiveFlushTimersRef.current.set(sessionId, timerId);
  }
}

export function flushAllPendingStreamBuffer(
  sessionId: string,
  deps: Pick<StreamWebSocketEventDeps, 'streamFlushRafRef' | 'progressiveFlushTimersRef'>,
): void {
  const buf = getSessionBuffer(sessionId);
  if (!buf) return;

  const pendingRaf = deps.streamFlushRafRef.current.get(sessionId);
  if (pendingRaf != null) {
    window.cancelAnimationFrame(pendingRaf);
    deps.streamFlushRafRef.current.delete(sessionId);
  }
  const pendingTimer = deps.progressiveFlushTimersRef.current.get(sessionId);
  if (pendingTimer != null) {
    window.clearTimeout(pendingTimer);
    deps.progressiveFlushTimersRef.current.delete(sessionId);
  }
  if (buf.chunks.length === 0) {
    setSessionStreaming(sessionId, false);
    setSessionBuffer(sessionId, null);
    return;
  }

  const allContent = buf.chunks.join('');
  updateSessionMessages(sessionId, (prev) => {
    const idx = prev.findIndex((message) => message.messageId === buf.messageId);
    if (idx >= 0) {
      const updated = [...prev];
      updated[idx] = {
        ...updated[idx],
        content: updated[idx].content + allContent,
        isStreaming: false,
      };
      return updated;
    }
    return prev;
  });
  setSessionStreaming(sessionId, false);
  setSessionBuffer(sessionId, null);
}

export function handleStreamWebSocketEvent(
  raw: Record<string, unknown>,
  evt: string | undefined,
  chunkSessionId: string,
  deps: StreamWebSocketEventDeps,
): boolean {
  if (!evt) return false;

  if (evt === 'message_chunk') {
    const chunk = raw as unknown as StreamChunk;
    const cSessionId = chunk.sessionId || chunkSessionId;
    const interruptedAt = deps.streamInterruptedAtRef.current.get(cSessionId);
    if (interruptedAt && Date.now() - interruptedAt < 800) {
      return true;
    }
    setSessionStreaming(cSessionId, !chunk.isFinal);
    deps.setStreamPhase('generating');

    const existingBuf = getSessionBuffer(cSessionId);
    if (!existingBuf || existingBuf.messageId !== chunk.messageId) {
      updateSessionMessages(cSessionId, preserveLatestThinkingPlaceholder);
      setSessionBuffer(cSessionId, {
        messageId: chunk.messageId,
        sessionId: cSessionId,
        chunks: [],
        isFinal: false,
      });
      deps.streamInterruptedAtRef.current.delete(cSessionId);
    }

    const buf = getSessionBuffer(cSessionId);
    if (buf) {
      const nextChunks = chunk.content ? [...buf.chunks, chunk.content] : buf.chunks;
      setSessionBuffer(cSessionId, {
        messageId: buf.messageId,
        sessionId: buf.sessionId,
        chunks: nextChunks,
        isFinal: buf.isFinal || !!chunk.isFinal,
      });
    }

    if (
      !deps.streamFlushRafRef.current.has(cSessionId) &&
      !deps.progressiveFlushTimersRef.current.has(cSessionId)
    ) {
      const raf = window.requestAnimationFrame(() => {
        deps.streamFlushRafRef.current.delete(cSessionId);
        flushStreamBuffer(cSessionId, deps);
      });
      deps.streamFlushRafRef.current.set(cSessionId, raf);
    }
    return true;
  }

  if (evt === 'stream_interrupted') {
    const iSessionId = chunkSessionId;
    deps.setStreamPhase('idle');
    deps.setActiveTools([]);
    deps.setCurrentAgentName('');
    setSessionStreaming(iSessionId, false);
    setSessionBuffer(iSessionId, null);
    const pendingRaf = deps.streamFlushRafRef.current.get(iSessionId);
    if (pendingRaf != null) {
      window.cancelAnimationFrame(pendingRaf);
      deps.streamFlushRafRef.current.delete(iSessionId);
    }
    const pendingTimer = deps.progressiveFlushTimersRef.current.get(iSessionId);
    if (pendingTimer != null) {
      window.clearTimeout(pendingTimer);
      deps.progressiveFlushTimersRef.current.delete(iSessionId);
    }
    deps.streamInterruptedAtRef.current.set(iSessionId, Date.now());
    updateSessionMessages(iSessionId, (prev) => {
      const updated = [...prev];
      let changed = false;
      for (let i = updated.length - 1; i >= 0; i -= 1) {
        if (updated[i].isStreaming) {
          if (!updated[i].content || updated[i].content.startsWith('正在')) {
            updated.splice(i, 1);
          } else {
            updated[i] = {
              ...updated[i],
              isStreaming: false,
              content: `${updated[i].content}\n\n[Interrupted, processing new message...]`,
            };
          }
          changed = true;
        }
      }
      return changed ? updated : prev;
    });
    return true;
  }

  if (evt === 'agent_thinking') {
    const payload = raw as unknown as {
      messageId: string;
      agentId: string;
      phase?: string;
      details?: string;
    };
    const interruptedAt = deps.streamInterruptedAtRef.current.get(chunkSessionId);
    if (interruptedAt && Date.now() - interruptedAt < 800) {
      return true;
    }
    if (interruptedAt) {
      deps.streamInterruptedAtRef.current.delete(chunkSessionId);
    }
    setSessionStreaming(chunkSessionId, true);
    const phase = payload.phase || '';
    if (phase === 'analyzing' || phase === 'planning') {
      deps.setStreamPhase('thinking');
    } else if (phase === 'executing') {
      deps.setStreamPhase('executing');
    } else if (phase === 'synthesizing') {
      deps.setStreamPhase('generating');
    }
    if (payload.agentId) deps.setCurrentAgentName(payload.agentId);

    updateSessionMessages(chunkSessionId, (prev) => {
      const optimisticIdx = prev.findIndex((message) => (message as Message & { _optimistic?: boolean })._optimistic && message.isStreaming);
      const existingIdx = prev.findIndex((message) => message.messageId === payload.messageId && message.isStreaming);
      if (optimisticIdx >= 0) {
        const updated = [...prev];
        updated[optimisticIdx] = {
          ...updated[optimisticIdx],
          messageId: payload.messageId,
          sender: payload.agentId || updated[optimisticIdx].sender,
          content: payload.details || '模型正在思考中...',
          _optimistic: undefined,
        };
        return updated;
      }
      if (existingIdx >= 0) {
        const updated = [...prev];
        updated[existingIdx] = {
          ...updated[existingIdx],
          content: payload.details || updated[existingIdx].content,
        };
        return updated;
      }
      return [
        ...prev,
        {
          event: 'message',
          sessionId: chunkSessionId,
          sender: payload.agentId || 'agent',
          content: payload.details || '',
          type: 'text',
          timestamp: new Date().toISOString(),
          messageId: payload.messageId,
          isStreaming: true,
        },
      ];
    });
    return true;
  }

  if (evt === 'tool_call') {
    const payload = raw as unknown as ToolCallEvent;
    deps.setStreamPhase('executing');
    if (payload.toolCalls && payload.toolCalls.length > 0) {
      deps.setActiveTools(payload.toolCalls.map((call) => call.name || ''));
    }
    updateSessionMessages(chunkSessionId, (prev) => {
      const lastThinkingIdx = (() => {
        for (let i = prev.length - 1; i >= 0; i -= 1) {
          if (isThinkingPlaceholder(prev[i])) return i;
        }
        return -1;
      })();
      let cleaned = prev;
      if (lastThinkingIdx >= 0) {
        cleaned = prev.map((message, index) => {
          if (index === lastThinkingIdx) {
            return { ...message, content: '🔧 正在执行工具...' };
          }
          if (isThinkingPlaceholder(message)) {
            return null;
          }
          return message;
        }).filter(Boolean) as Message[];
      }
      return [
        ...cleaned,
        {
          event: 'message',
          sessionId: chunkSessionId,
          sender: 'system',
          content: '',
          type: 'tool_call',
          timestamp: payload.timestamp,
          messageId: payload.messageId,
          toolCallData: { calls: payload.toolCalls },
        },
      ];
    });
    return true;
  }

  if (evt === 'tool_result') {
    const payload = raw as unknown as ToolResultEvent;
    if (payload.results) {
      deps.setActiveTools((prev) => prev.filter(
        (name) => !payload.results.some((result) => result.tool_name === name),
      ));
      for (const result of payload.results) {
        if (!result.success) {
          deps.addToast({
            type: 'error',
            title: `工具执行失败: ${result.tool_name}`,
            message: result.error || '未知错误',
            duration: 8000,
          });
        }
      }
    }
    updateSessionMessages(chunkSessionId, (prev) => {
      const updated = [...prev];
      for (let i = updated.length - 1; i >= 0; i -= 1) {
        if (updated[i].type === 'tool_call' && updated[i].messageId === payload.messageId) {
          updated[i] = {
            ...updated[i],
            type: 'tool_result',
            toolResultData: { results: payload.results },
          };
          break;
        }
      }
      return updated;
    });

    if (payload.results) {
      for (const result of payload.results) {
        if (!result.success || !result.result) continue;
        const r = result.result as Record<string, unknown>;
        const filePath = r.path as string | undefined;
        if (!filePath) continue;

        const content = r.content as string | undefined;
        if (content && typeof content === 'string') {
          const ext = filePath.split('.').pop()?.toLowerCase() || '';
          const isPreviewable = /^(py|js|ts|jsx|tsx|java|go|rs|c|cpp|h|hpp|swift|kt|rb|php|sql|sh|bash|vue|svelte|astro|html|css|scss|less|json|yaml|yml|xml|toml|ini|md|txt|cfg|conf|env|dockerfile|makefile|graphql|proto)$/i.test(ext);
          if (isPreviewable && content.length < 500000) {
            deps.handleOpenFilePreview(filePath, content, undefined, r.status as string | undefined);
          }
        }

        const diff = r.diff as string | undefined;
        if (diff && typeof diff === 'string' && diff.length > 0) {
          deps.handleOpenDiffPreview(filePath, diff);
        }
      }
    }
    return true;
  }

  if (evt === 'message') {
    deps.setStreamPhase('done');
    deps.setActiveTools([]);
    window.setTimeout(() => deps.setStreamPhase('idle'), 2000);

    flushAllPendingStreamBuffer(chunkSessionId, deps);
    const msg = raw as unknown as Message;
    const isSystemMsg = msg.type === 'system' || msg.sender === 'system';
    setSessionStreaming(chunkSessionId, false);
    updateSessionMessages(chunkSessionId, (prev) => mergeFinalMessage(prev, msg));
    if (!isSystemMsg) {
      deps.setSessions((prev) => deps.sortSessions(
        prev.map((session) => (session.id === (msg.sessionId || chunkSessionId)
          ? { ...session, lastMessageAt: msg.timestamp || new Date().toISOString() }
          : session)),
      ));
    }
    if (msg.symbolic?.generated) {
      deps.setGenerated(msg.symbolic.generated as GeneratedData);
      const gen = msg.symbolic.generated as GeneratedData;
      if (gen.fileDetails && gen.fileDetails.length > 0) {
        for (const fd of gen.fileDetails) {
          if (fd.path && fd.content && fd.content.length < 500000) {
            deps.handleOpenFilePreview(fd.path, fd.content);
          }
        }
      }
      if (gen.diff) {
        deps.handleOpenDiffPreview('changes.diff', gen.diff);
      }
    }
    return true;
  }

  return false;
}
