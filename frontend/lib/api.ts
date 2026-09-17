/**
 * Authenticated fetch helpers shared across the IM page and future modules.
 *
 * Extracted from the page shell (R3/R4 hot-module thinning). `authHeaders`
 * reads the token from localStorage; `fetchAuth` wraps fetch and reports 401
 * through a caller-supplied callback so expired sessions can be handled once.
 */

export function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const localToken = typeof window !== 'undefined' ? localStorage.getItem('agenthub_token') : '';
  return localToken ? { ...extra, Authorization: `Bearer ${localToken}` } : extra;
}

/** Fetch wrapper that attaches auth headers and calls onUnauthorized on 401. */
export async function fetchAuth(
  url: string,
  onUnauthorized: () => void,
  init: RequestInit = {},
): Promise<Response> {
  const res = await fetch(url, {
    ...init,
    headers: { ...authHeaders(), ...((init.headers as Record<string, string>) || {}) },
  });
  if (res.status === 401) {
    onUnauthorized();
    throw new Error('TOKEN_EXPIRED');
  }
  return res;
}