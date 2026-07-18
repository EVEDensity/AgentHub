const DEFAULT_WS_BASE_URL = 'ws://127.0.0.1:8000';

function normalizeWebSocketBaseUrl(raw: string): string {
  const trimmed = raw.trim().replace(/\/$/, '');
  if (trimmed.startsWith('ws://') || trimmed.startsWith('wss://')) {
    return trimmed;
  }
  if (trimmed.startsWith('http://')) {
    return `ws://${trimmed.slice('http://'.length)}`;
  }
  if (trimmed.startsWith('https://')) {
    return `wss://${trimmed.slice('https://'.length)}`;
  }
  return trimmed;
}

export function buildChatWebSocketUrl(sessionId: string, token: string): string {
  const configuredBase = process.env.NEXT_PUBLIC_WS_URL?.trim();
  const baseUrl = configuredBase ? normalizeWebSocketBaseUrl(configuredBase) : DEFAULT_WS_BASE_URL;
  return `${baseUrl}/ws/${encodeURIComponent(sessionId)}?token=${encodeURIComponent(token)}`;
}
