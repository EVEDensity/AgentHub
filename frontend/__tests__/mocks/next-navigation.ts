/**
 * Mock for next/navigation — App Router hooks.
 */
import { vi } from 'vitest';

export const useRouter = vi.fn(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  refresh: vi.fn(),
  back: vi.fn(),
  prefetch: vi.fn(),
}));

export const useSearchParams = vi.fn(() => ({
  get: vi.fn((key: string) => null),
  has: vi.fn(() => false),
  toString: vi.fn(() => ''),
}));

export const usePathname = vi.fn(() => '/');

export const useParams = vi.fn(() => ({}));

export const notFound = vi.fn();
export const redirect = vi.fn();
