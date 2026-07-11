import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// Reset the store module for each test so we get a fresh store instance
let useAuthStore: ReturnType<typeof import('../../stores/authStore').useAuthStore.getState> extends infer S
  ? typeof import('../../stores/authStore').useAuthStore
  : never;

// We import dynamically to get a fresh copy per test
async function getStore() {
  // Clear module cache to get a fresh zustand store
  const mod = await import('../../stores/authStore');
  return mod.useAuthStore;
}

describe('authStore', () => {
  beforeEach(() => {
    vi.resetModules();
    window.localStorage.clear();
  });

  // ── Initial state ──────────────────────────────────────────────

  it('starts with null user and empty token', async () => {
    const store = await getStore();
    const state = store.getState();
    expect(state.user).toBeNull();
    expect(state.token).toBe('');
  });

  // ── setUser / setToken ─────────────────────────────────────────

  it('setUser updates the user object', async () => {
    const store = await getStore();
    store.getState().setUser({ id: '1', name: 'Alice' } as any);
    expect(store.getState().user).toEqual({ id: '1', name: 'Alice' });
  });

  it('setUser accepts null to clear user', async () => {
    const store = await getStore();
    store.getState().setUser({ id: '1', name: 'Alice' } as any);
    store.getState().setUser(null);
    expect(store.getState().user).toBeNull();
  });

  it('setToken updates the token', async () => {
    const store = await getStore();
    store.getState().setToken('abc123');
    expect(store.getState().token).toBe('abc123');
  });

  // ── authHeaders ────────────────────────────────────────────────

  it('returns Bearer token header when token is set', async () => {
    const store = await getStore();
    store.getState().setToken('my-token');
    const headers = store.getState().authHeaders();
    expect(headers).toEqual({ Authorization: 'Bearer my-token' });
  });

  it('returns empty object when token is empty', async () => {
    const store = await getStore();
    const headers = store.getState().authHeaders();
    expect(headers).toEqual({});
  });

  it('reads token from localStorage if state token is empty', async () => {
    window.localStorage.setItem('agenthub_token', 'stored-token');
    const store = await getStore();
    const headers = store.getState().authHeaders();
    expect(headers).toEqual({ Authorization: 'Bearer stored-token' });
  });

  it('prefers state token over localStorage token', async () => {
    window.localStorage.setItem('agenthub_token', 'ls-token');
    const store = await getStore();
    store.getState().setToken('state-token');
    const headers = store.getState().authHeaders();
    expect(headers).toEqual({ Authorization: 'Bearer state-token' });
  });

  // ── fmtErr ─────────────────────────────────────────────────────

  it('returns the string detail directly', async () => {
    const store = await getStore();
    expect(store.getState().fmtErr('Something broke', 'fallback')).toBe('Something broke');
  });

  it('joins array of msg objects', async () => {
    const store = await getStore();
    const detail = [{ msg: 'Error A' }, { msg: 'Error B' }];
    expect(store.getState().fmtErr(detail, 'fallback')).toBe('Error A; Error B');
  });

  it('filters out items without msg property', async () => {
    const store = await getStore();
    const detail = [{ msg: 'Error A' }, { foo: 'bar' }];
    expect(store.getState().fmtErr(detail, 'fallback')).toBe('Error A');
  });

  it('returns fallback when array is empty or all items lack msg', async () => {
    const store = await getStore();
    expect(store.getState().fmtErr([{ foo: 'bar' }], 'fallback')).toBe('fallback');
  });

  it('returns fallback for non-string, non-array input', async () => {
    const store = await getStore();
    expect(store.getState().fmtErr(42, 'fallback')).toBe('fallback');
    expect(store.getState().fmtErr(null, 'fallback')).toBe('fallback');
    expect(store.getState().fmtErr(undefined, 'fallback')).toBe('fallback');
    expect(store.getState().fmtErr({ code: 500 }, 'fallback')).toBe('fallback');
  });
});
