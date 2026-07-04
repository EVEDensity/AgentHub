import type { AppProps } from 'next/app';
import type { JSX } from 'react';
import { useEffect, useState, createContext, useContext, useCallback } from 'react';
import { LazyMotion, domAnimation, AnimatePresence } from 'framer-motion';
import '../styles/globals.css';

/**
 * AgentHub App — global providers, animations, theme, and accessibility.
 *
 * - LazyMotion (domAnimation) reduces initial bundle.
 * - AnimatePresence wraps page-level transitions.
 * - ThemeProvider persists theme to localStorage and sets data-theme on <html>.
 * - Detects prefers-reduced-motion for WCAG 2.1 compliance.
 */

// ── Theme Context ────────────────────────────────────────────────────

export type Theme = 'warm' | 'light' | 'dark';

interface ThemeContextValue {
  theme: Theme;
  setTheme: (t: Theme) => void;
  toggleTheme: () => void;
}

const ThemeContext = createContext<ThemeContextValue>({
  theme: 'warm',
  setTheme: () => {},
  toggleTheme: () => {},
});

export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext);
}

const THEME_STORAGE_KEY = 'agenthub_theme';

function getStoredTheme(): Theme {
  if (typeof window === 'undefined') return 'warm';
  const stored = localStorage.getItem(THEME_STORAGE_KEY);
  if (stored === 'light' || stored === 'dark' || stored === 'warm') return stored;
  // Also check for legacy key
  const legacy = localStorage.getItem('agenthub_theme');
  if (legacy === 'light' || legacy === 'dark' || legacy === 'warm') return legacy;
  return 'warm';
}

function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute('data-theme', theme);
  // Set color-scheme for native browser elements (scrollbars, form controls)
  document.documentElement.style.colorScheme =
    theme === 'dark' ? 'dark' : 'light';
}

// ── Reduced Motion ───────────────────────────────────────────────────

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    setReduced(mq.matches);
    const handler = (e: MediaQueryListEvent) => setReduced(e.matches);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);
  return reduced;
}

// ── App Component ────────────────────────────────────────────────────

export default function App({ Component, pageProps, router }: AppProps): JSX.Element {
  const [theme, setThemeState] = useState<Theme>('warm');
  const [themeReady, setThemeReady] = useState(false);
  const reducedMotion = useReducedMotion();

  // Initialize theme from localStorage on mount (before first paint flash)
  useEffect(() => {
    const stored = getStoredTheme();
    setThemeState(stored);
    applyTheme(stored);
    setThemeReady(true);
  }, []);

  const setTheme = useCallback((t: Theme) => {
    setThemeState(t);
    applyTheme(t);
    localStorage.setItem(THEME_STORAGE_KEY, t);
    // Legacy key for compatibility with GeneralSettingsModule
  }, []);

  const toggleTheme = useCallback(() => {
    setThemeState((prev) => {
      const next = prev === 'dark' ? 'warm' : 'dark';
      return next;
    });
    // Need to re-read state to apply — use nested approach
    const current = document.documentElement.getAttribute('data-theme') as Theme || 'warm';
    const next = current === 'dark' ? 'warm' : 'dark';
    applyTheme(next);
    localStorage.setItem(THEME_STORAGE_KEY, next);
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle('motion-reduce', reducedMotion);
  }, [reducedMotion]);

  // Theme flash prevention: render nothing until theme is read from localStorage
  // This prevents the default "warm" theme from flashing before the user's preference loads.
  if (!themeReady) {
    return <div style={{ visibility: 'hidden' }} />;
  }

  return (
    <ThemeContext.Provider value={{ theme, setTheme, toggleTheme }}>
      <LazyMotion features={domAnimation} strict>
        <AnimatePresence mode="wait" initial={false}>
          <Component {...pageProps} key={router.route} />
        </AnimatePresence>
      </LazyMotion>
    </ThemeContext.Provider>
  );
}
