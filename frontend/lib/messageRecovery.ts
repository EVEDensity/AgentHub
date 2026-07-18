import type { Message } from '../types';

const DEFAULT_REPLAY_CAP = 500;
const DEFAULT_REPLAY_TRIM_TO = 250;

function isStreamingThinkingMessage(message: Message): boolean {
  return !!(message.isStreaming && message.sender !== 'user' && !message.diffFilePath && message.type === 'text');
}

function isSystemConnectedMessage(message: Message): boolean {
  return (message.type === 'system' || message.sender === 'system') && message.content.includes('已连接');
}

export function registerReplayMessageId(seenIds: Set<string>, messageId: string, cap = DEFAULT_REPLAY_CAP): Set<string> {
  if (!messageId) return seenIds;
  if (seenIds.has(messageId)) return seenIds;
  const next = new Set(seenIds);
  next.add(messageId);
  if (next.size <= cap) return next;
  const arr = [...next];
  return new Set(arr.slice(arr.length - DEFAULT_REPLAY_TRIM_TO));
}

export function mergeReloadedMessages(prev: Message[], incoming: Message[]): Message[] {
  const existingIds = new Set(prev.filter((m) => m.id).map((m) => m.id as string));
  const newMessages = incoming.filter((m) => !m.id || !existingIds.has(m.id));
  if (newMessages.length === 0) {
    return prev;
  }
  const clean = prev.filter((m) => !isStreamingThinkingMessage(m));
  return [...clean, ...newMessages].sort((a, b) => a.timestamp.localeCompare(b.timestamp));
}

export function mergeFinalMessage(prev: Message[], incoming: Message): Message[] {
  const clean = prev.filter((message) => !isStreamingThinkingMessage(message));
  const isSystemMsg = incoming.type === 'system' || incoming.sender === 'system';
  const targetMessageId = incoming.messageId || incoming.id || '';
  const streamingIdx = targetMessageId
    ? clean.findIndex((message) => message.messageId === targetMessageId)
    : -1;

  if (streamingIdx >= 0 && !isSystemMsg) {
    const updated = [...clean];
    updated[streamingIdx] = { ...incoming, messageId: undefined, isStreaming: false };
    if (incoming.id) {
      return updated.filter((message, index) => index === streamingIdx || message.id !== incoming.id);
    }
    return updated;
  }

  if (isSystemConnectedMessage(incoming)) {
    return clean;
  }

  if (incoming.id && clean.some((message) => message.id === incoming.id)) {
    return clean;
  }

  return [...clean, { ...incoming, messageId: undefined, isStreaming: false }];
}
