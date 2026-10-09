/** Local transport association; the server remains the session scope authority. */
const CACHE_KEY = 'agenthub_v1_chat_session_links';

export function readMissionSession(key: string): string | undefined {
  try {
    const value = JSON.parse(localStorage.getItem(CACHE_KEY) ?? '{}')[key];
    return typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(value) ? value : undefined;
  } catch {
    return undefined;
  }
}

export function rememberMissionSession(key: string, sessionId: string): void {
  try {
    const values = JSON.parse(localStorage.getItem(CACHE_KEY) ?? '{}');
    const workspacePrefix = key.slice(0, key.indexOf('/') + 1);
    localStorage.setItem(CACHE_KEY, JSON.stringify({ ...values, [key]: sessionId, [workspacePrefix + sessionId]: sessionId }));
  } catch {
    // The current mounted conversation can still use the admitted session.
  }
}
