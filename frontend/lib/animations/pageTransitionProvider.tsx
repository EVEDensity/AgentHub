'use client';

import { type JSX, type ReactNode, createContext, useContext, useEffect, useState } from 'react';
import { MotionConfig } from 'framer-motion';

/**
 * PageTransitionProvider
 *
 * Client-side provider that:
 * 1. Respects `prefers-reduced-motion` OS setting — disables all
 *    animations when the user has requested reduced motion (WCAG 2.3.3).
 * 2. Wraps children in framer-motion MotionConfig for centralized
 *    animation configuration.
 * 3. Provides `useReducedMotion()` hook for components to query.
 *
 * Part of AgentHub V5.1 P2-4 + §6.4 (WCAG 2.1 AA)
 */

// ── Context ─────────────────────────────────────────────────────

interface MotionContextValue {
  /** Whether the user has requested reduced motion (WCAG 2.3.3) */
  reducedMotion: boolean;
}

const MotionContext = createContext<MotionContextValue>({ reducedMotion: false });

/**
 * Hook: check whether the user prefers reduced motion.
 * Use this to conditionally disable animations, particles, etc.
 */
export function useReducedMotion(): boolean {
  return useContext(MotionContext).reducedMotion;
}

// ── Provider ────────────────────────────────────────────────────

export function PageTransitionProvider({
  children,
}: {
  children: ReactNode;
}): JSX.Element {
  const [reducedMotion, setReducedMotion] = useState(false);

  useEffect(() => {
    const mql = window.matchMedia('(prefers-reduced-motion: reduce)');
    setReducedMotion(mql.matches);

    const handler = (e: MediaQueryListEvent) => {
      setReducedMotion(e.matches);
    };
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  }, []);

  return (
    <MotionContext.Provider value={{ reducedMotion }}>
      <MotionConfig
        reducedMotion={reducedMotion ? 'always' : 'never'}
        transition={reducedMotion ? { duration: 0 } : undefined}
      >
        {children}
      </MotionConfig>
    </MotionContext.Provider>
  );
}
