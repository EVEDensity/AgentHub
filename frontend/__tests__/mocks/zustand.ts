/**
 * Zustand mock helper — creates a per-test mock store that preserves the
 * original store's shape while allowing test-specific overrides.
 *
 * Usage:
 *   const mockStore = createMockStore(useMyStore, { field: overrideValue });
 *
 * Returns the mock function so you can assert calls on it.
 */
import { vi, type Mock } from 'vitest';

type StoreFn = (...args: any[]) => any;

export function mockStore<T extends StoreFn>(
  storeHook: T,
  overrides?: Partial<ReturnType<T>>
): Mock {
  const original = storeHook as unknown as (...args: any[]) => any;
  // The default selector is (s) => s (the whole state)
  const defaultState = original((s: any) => s) || {};
  const merged = { ...defaultState, ...overrides };
  return vi.fn((selector?: (state: any) => any) => {
    if (typeof selector === 'function') {
      return selector(merged);
    }
    return merged;
  });
}
