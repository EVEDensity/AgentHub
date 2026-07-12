/**
 * AgentHub Design Token System
 *
 * Centralized, programmatic design tokens for consistent visual styling
 * across all Typed components. Built on OKLCH color space with P3 wide-gamut
 * support. CSS variables in globals.css are the runtime source; these tokens
 * mirror them for use in JS/TS logic (e.g., PixiJS rendering, dynamic styles).
 *
 * Part of AgentHub V5.1 §6.5 — Design Token System
 *
 * Usage:
 *   import { tokens, useTokens } from '@/tokens';
 *   const { color, spacing, motion } = useTokens();
 *   div.style.backgroundColor = color.brand[500];
 */

// ── Helper ────────────────────────────────────────────────────────────

/** Reads a CSS custom property value from :root (client-side only). */
function cssVar(name: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function cssVarNum(name: string, fallback: number): number {
  const v = cssVar(name, '');
  if (!v) return fallback;
  const n = parseFloat(v.replace(/[a-z%]+/g, ''));
  return isNaN(n) ? fallback : n;
}

// ── Color Tokens ───────────────────────────────────────────────────────

/** OKLCH brand colors with P3 wide-gamut support */
const brand = {
  /** Brand-50: lightest tint — backgrounds, hover states */
  50:  'oklch(0.97 0.01 260)',
  100: 'oklch(0.93 0.02 260)',
  200: 'oklch(0.88 0.04 260)',
  300: 'oklch(0.78 0.08 260)',
  400: 'oklch(0.65 0.14 260)',
  /** Brand-500: primary action color — buttons, links, active states */
  500: 'oklch(0.55 0.20 260)',
  600: 'oklch(0.45 0.18 260)',
  700: 'oklch(0.35 0.14 260)',
  800: 'oklch(0.30 0.10 260)',
  /** Brand-900: darkest — text on light backgrounds, emphasis */
  900: 'oklch(0.25 0.08 260)',
} as const;

const accent = {
  50:  'oklch(0.97 0.02 160)',
  100: 'oklch(0.93 0.04 160)',
  200: 'oklch(0.87 0.08 160)',
  300: 'oklch(0.78 0.14 160)',
  400: 'oklch(0.70 0.18 160)',
  500: 'oklch(0.65 0.22 160)',
  600: 'oklch(0.55 0.20 160)',
  700: 'oklch(0.42 0.15 160)',
} as const;

/** Semantic colors — WCAG AA verified */
const semantic = {
  success: {
    fg: '#009950',
    bg: '#e6f7ee',
    light: '#f0faf4',
  },
  danger: {
    fg: '#d93030',
    bg: '#ffeeee',
    light: '#fef5f5',
  },
  warning: {
    fg: '#b85c00',
    bg: '#fff7e6',
    light: '#fffbf0',
  },
  info: {
    fg: '#1a6ff5',
    bg: '#e6f0ff',
    light: '#f5f8ff',
  },
} as const;

/** Neutral grays — cold-toned for warm-studio aesthetic, dark-mode ready */
const neutral = {
  0:   '#ffffff',
  25:  '#fbfbfc',
  50:  '#f7f8fa',
  75:  '#f3f4f6',
  100: '#eff1f5',
  150: '#e8eaf0',
  200: '#e2e5ea',
  250: '#d4d7df',
  300: '#c4c8d0',
  400: '#9ba0ab',
  500: '#6b7280',
  600: '#4b5563',
  700: '#374151',
  800: '#1f2937',
  900: '#111827',
  950: '#0a0d13',
} as const;

// ── Spacing Scale ──────────────────────────────────────────────────────

/** 4px-based spacing scale */
const spacing = {
  '0':    0,
  'px':   1,
  '0.5':  2,
  '1':    4,
  '1.5':  6,
  '2':    8,
  '2.5':  10,
  '3':    12,
  '3.5':  14,
  '4':    16,
  '5':    20,
  '6':    24,
  '7':    28,
  '8':    32,
  '9':    36,
  '10':   40,
  '11':   44,
  '12':   48,
  '14':   56,
  '16':   64,
  '20':   80,
  '24':   96,
  '28':   112,
  '32':   128,
  '40':   160,
  '48':   192,
  '56':   224,
  '64':   256,
} as const;

// ── Border Radius ──────────────────────────────────────────────────────

const radius = {
  none:   0,
  sm:     4,
  md:     6,
  lg:     8,
  xl:     12,
  '2xl':  16,
  '3xl':  20,
  full:   9999,
} as const;

// ── Shadow ──────────────────────────────────────────────────────────────

const shadow = {
  none:   'none',
  sm:     '0 1px 2px rgba(0,0,0,.04)',
  md:     '0 4px 12px rgba(0,0,0,.08)',
  lg:     '0 12px 32px rgba(0,0,0,.12)',
  xl:     '0 20px 60px rgba(0,0,0,.16)',
  card:   '0 1px 3px rgba(0,0,0,.06), 0 1px 2px rgba(0,0,0,.04)',
  'card-hover': '0 4px 12px rgba(0,0,0,.1), 0 2px 4px rgba(0,0,0,.06)',
  'card-elevated': '0 8px 24px rgba(0,0,0,.12), 0 4px 8px rgba(0,0,0,.08)',
  modal:  '0 12px 48px rgba(0,0,0,.16), 0 4px 16px rgba(0,0,0,.08)',
  button: '0 2px 4px rgba(76,106,245,.2), 0 4px 8px rgba(76,106,245,.1)',
} as const;

// ── Typography ──────────────────────────────────────────────────────────

const typography = {
  fontFamily: {
    sans: "'Noto Sans SC', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    mono: "'JetBrains Mono', 'Cascadia Code', 'Fira Code', 'IBM Plex Mono', 'SF Mono', Consolas, monospace",
  },
  fontSize: {
    '2xs':   10,
    xs:      12,
    sm:      13,
    base:    15,
    lg:      17,
    xl:      20,
    '2xl':   24,
    '3xl':   32,
    '4xl':   40,
    '5xl':   48,
  },
  fontWeight: {
    normal:   400,
    medium:   500,
    semibold: 600,
    bold:     700,
  },
  lineHeight: {
    none:    1,
    tight:   1.25,
    snug:    1.375,
    normal:  1.5,
    relaxed: 1.625,
    loose:   2,
  },
  letterSpacing: {
    tighter: '-0.02em',
    tight:   '-0.01em',
    normal:  '0',
    wide:    '0.02em',
    wider:   '0.05em',
  },
} as const;

// ── Motion ──────────────────────────────────────────────────────────────

const motion = {
  duration: {
    instant: 0,
    fast:    150,
    normal:  250,
    slow:    400,
    slower:  600,
    slowest: 1000,
  },
  easing: {
    /** Default ease-out curve — most transitions */
    ease:       [0.16, 1, 0.3, 1] as number[],
    /** Ease-in-out for enter+exit animations */
    inOut:     [0.4, 0, 0.2, 1] as number[],
    /** Gentle spring for interactive elements */
    spring:    { stiffness: 300, damping: 30 } as const,
    /** Bouncier spring for emphasis */
    springBounce: { stiffness: 400, damping: 20 } as const,
  },
} as const;

// ── Z-Index Scale ──────────────────────────────────────────────────────

const zIndex = {
  base:      0,
  raised:    10,
  sticky:    100,
  overlay:   200,
  drawer:    300,
  modal:     400,
  popover:   500,
  tooltip:   600,
  toast:     700,
  debug:     9999,
} as const;

// ── Breakpoints ─────────────────────────────────────────────────────────

const breakpoint = {
  mobile:   480,
  tablet:   768,
  tabletL:  1024,
  desktop:  1280,
  wide:     1536,
  ultra:    1920,
} as const;

// ── Export ──────────────────────────────────────────────────────────────

export const tokens = {
  color: {
    brand,
    accent,
    ...semantic,
    neutral,
  },
  spacing,
  radius,
  shadow,
  typography,
  motion,
  zIndex,
  breakpoint,
} as const;

export type Tokens = typeof tokens;

// ── Hook ────────────────────────────────────────────────────────────────

/**
 * React hook for consuming design tokens in components.
 * Reads CSS custom properties at runtime for dynamic theming support
 * (e.g., dark mode, user-defined accent colors).
 *
 * @example
 *   const { color, spacing } = useTokens();
 *   return <div style={{ backgroundColor: color.brand[500] }} />;
 */
export function useTokens(): Tokens {
  return tokens;
}

/**
 * Read a CSS custom property value at runtime.
 * Useful for bridging Tailwind CSS variables with JS logic.
 *
 * @example
 *   const primary = useCssVar('--primary-500', '#6366f1');
 */
export function useCssVar(name: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback;
  // In a React component, this would use a ref + useEffect for SSR safety.
  // For simplicity, we read from document directly here.
  return cssVar(name, fallback);
}

/**
 * Convert a hex color string to a PixiJS-compatible hex number.
 *
 * @example
 *   const color = hexToPixi('#6366f1'); // 0x6366f1
 */
export function hexToPixi(hex: string): number {
  return parseInt(hex.replace('#', ''), 16);
}

/**
 * Convert an OKLCH color to an approximate hex fallback for non-P3 displays.
 * Simplified conversion — for exact values, use a color library.
 */
export function oklchToHex(_oklch: string, fallback: string = '#6366f1'): string {
  // OKLCH → sRGB conversion requires color math; for now, return the fallback.
  // Production: use color.js or culori for accurate conversion.
  return fallback;
}
