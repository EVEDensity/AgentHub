// Internationalization Framework (P2-5)
// Lightweight i18n with lazy-loaded locale bundles.
// Uses React Context for locale state across the app.
//
// Usage:
//   import { useI18n } from '@/lib/i18n';
//   const { t, locale, setLocale } = useI18n();
//   <h1>{t('admin.title')}</h1>

'use client';

import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  type JSX,
  type ReactNode,
} from 'react';

// ── Types ──────────────────────────────────────────────────────────────

export type Locale = 'zh-CN' | 'en';

export interface LocaleBundle {
  [key: string]: string | LocaleBundle;
}

interface I18nContextValue {
  locale: Locale;
  setLocale: (l: Locale) => void;
  t: (key: string, params?: Record<string, string | number>) => string;
  localeName: string;
}

// ── Locale Bundles ─────────────────────────────────────────────────────

const bundles: Record<Locale, () => Promise<{ default: LocaleBundle }>> = {
  'zh-CN': () => import('./zh-CN'),
  'en': () => import('./en'),
};

// ── Context ────────────────────────────────────────────────────────────

const I18nContext = createContext<I18nContextValue | null>(null);

// ── Provider ───────────────────────────────────────────────────────────

export function I18nProvider({ children, defaultLocale = 'zh-CN' }: {
  children: ReactNode;
  defaultLocale?: Locale;
}): JSX.Element {
  const [locale, setLocaleState] = useState<Locale>(() => {
    if (typeof window !== 'undefined') {
      const stored = localStorage.getItem('agenthub_locale') as Locale | null;
      if (stored === 'zh-CN' || stored === 'en') return stored;
    }
    return defaultLocale;
  });

  const [messages, setMessages] = useState<LocaleBundle>({});

  useEffect(() => {
    let cancelled = false;
    bundles[locale]().then((mod) => {
      if (!cancelled) setMessages(mod.default);
    });
    return () => { cancelled = true; };
  }, [locale]);

  const setLocale = useCallback((l: Locale) => {
    setLocaleState(l);
    if (typeof window !== 'undefined') {
      localStorage.setItem('agenthub_locale', l);
      document.documentElement.lang = l;
    }
  }, []);

  const t = useCallback((key: string, params?: Record<string, string | number>): string => {
    const keys = key.split('.');
    let value: unknown = messages;
    for (const k of keys) {
      if (value && typeof value === 'object' && k in value) {
        value = (value as Record<string, unknown>)[k];
      } else {
        // Fallback: return the key path itself
        return key;
      }
    }
    let result = typeof value === 'string' ? value : key;

    // Interpolate {{param}} placeholders
    if (params) {
      result = result.replace(/\{\{(\w+)\}\}/g, (_, name) => {
        return params[name] !== undefined ? String(params[name]) : `{{${name}}}`;
      });
    }
    return result;
  }, [messages]);

  const localeName = locale === 'zh-CN' ? '中文' : 'English';

  return (
    <I18nContext.Provider value={{ locale, setLocale, t, localeName }}>
      {children}
    </I18nContext.Provider>
  );
}

// ── Hook ───────────────────────────────────────────────────────────────

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext);
  if (!ctx) {
    // Non-hook fallback for environments without provider
    return {
      locale: 'zh-CN',
      setLocale: () => {},
      t: (key: string) => key,
      localeName: '中文',
    };
  }
  return ctx;
}

// ── Localized Date Formatting ──────────────────────────────────────────

export function formatDate(date: Date | string, locale: Locale): string {
  const d = typeof date === 'string' ? new Date(date) : date;
  return new Intl.DateTimeFormat(locale === 'zh-CN' ? 'zh-CN' : 'en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(d);
}

export function formatRelativeTime(date: Date | string, locale: Locale): string {
  const d = typeof date === 'string' ? new Date(date) : date;
  const now = Date.now();
  const diff = now - d.getTime();
  const mins = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days = Math.floor(diff / 86400000);

  if (locale === 'zh-CN') {
    if (mins < 1) return '刚刚';
    if (mins < 60) return `${mins} 分钟前`;
    if (hours < 24) return `${hours} 小时前`;
    if (days < 30) return `${days} 天前`;
  } else {
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    if (hours < 24) return `${hours}h ago`;
    if (days < 30) return `${days}d ago`;
  }
  return formatDate(date, locale);
}

// ── Locale Switcher Component ──────────────────────────────────────────

export function LocaleSwitcher({ className = '' }: { className?: string }): JSX.Element {
  const { locale, setLocale, localeName } = useI18n();

  return (
    <button
      onClick={() => setLocale(locale === 'zh-CN' ? 'en' : 'zh-CN')}
      className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium
        bg-warm-100 hover:bg-warm-200 text-warm-700 transition-colors ${className}`}
      title={locale === 'zh-CN' ? 'Switch to English' : '切换到中文'}
    >
      <span className="text-sm leading-none">{locale === 'zh-CN' ? '🇨🇳' : '🇺🇸'}</span>
      <span>{localeName}</span>
    </button>
  );
}
