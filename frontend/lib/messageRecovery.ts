import type { Message } from '../types';

const DEFAULT_REPLAY_CAP = 500;
const DEFAULT_REPLAY_TRIM_TO = 250;

function isStreamingThinkingMessage(message: Message): boolean {
  return !!(message.isStreaming && message.sender !== 'user' && !message.diffFilePath && message.type === 'text');
}

function isSystemConnectedMessage(message: Message): boolean {
  return (message.type === 'system' || message.sender === 'system') && message.content.includes('已连接');
}

function getMessageIdentity(message: Message): string {
  return message.id || message.messageId || '';
}

function dedupeMessagesByIdentity(messages: Message[]): Message[] {
  const seen = new Map<string, Message>();
  const anonymous: Message[] = [];

  for (const message of messages) {
    const identity = getMessageIdentity(message);
    if (!identity) {
      anonymous.push(message);
      continue;
    }
    seen.set(identity, message);
  }

  return [...anonymous, ...seen.values()];
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
  const existingIds = new Set(prev.map(getMessageIdentity).filter(Boolean));
  const newMessages = incoming.filter((message) => {
    const identity = getMessageIdentity(message);
    return !identity || !existingIds.has(identity);
  });
  const clean = prev.filter((m) => !isStreamingThinkingMessage(m));
  const merged = dedupeMessagesByIdentity([...clean, ...newMessages]);
  if (newMessages.length === 0 && clean.length === prev.length && merged.length === prev.length) {
    return prev;
  }
  return merged.sort((a, b) => a.timestamp.localeCompare(b.timestamp));
}

export function mergeFinalMessage(prev: Message[], incoming: Message): Message[] {
  const clean = prev.filter((message) => !isStreamingThinkingMessage(message));
  const isSystemMsg = incoming.type === 'system' || incoming.sender === 'system';
  const incomingIdentity = getMessageIdentity(incoming);
  const targetMessageId = incoming.messageId || incoming.id || '';
  const streamingIdx = targetMessageId
    ? clean.findIndex((message) => message.messageId === targetMessageId)
    : -1;

  if (streamingIdx >= 0 && !isSystemMsg) {
    const updated = [...clean];
    updated[streamingIdx] = { ...incoming, messageId: undefined, isStreaming: false };
    if (incomingIdentity) {
      return updated.filter((message, index) => index === streamingIdx || getMessageIdentity(message) !== incomingIdentity);
    }
    return updated;
  }

  if (isSystemConnectedMessage(incoming)) {
    return clean;
  }

  if (incomingIdentity && clean.some((message) => getMessageIdentity(message) === incomingIdentity)) {
    return clean;
  }

  return dedupeMessagesByIdentity([...clean, { ...incoming, messageId: undefined, isStreaming: false }]);
}
