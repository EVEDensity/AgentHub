// Page Transition Animations (P2-4)
// framer-motion variants for page transitions, staggered lists,
// and skeleton morphing effects.

import type { Variants } from 'framer-motion';

// ── Page Transition Variants ───────────────────────────────────────

/** Fade + slide-up page enter/exit — for AnimatePresence at module level */
export const pageVariants: Variants = {
  initial: {
    opacity: 0,
    y: 12,
    scale: 0.995,
  },
  animate: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: {
      duration: 0.28,
      ease: [0.16, 1, 0.3, 1], // custom ease-out expo
    },
  },
  exit: {
    opacity: 0,
    y: -8,
    scale: 0.995,
    transition: {
      duration: 0.18,
      ease: [0.4, 0, 1, 1],
    },
  },
};

/** Fade-only transition — lighter, for inner tab switches */
export const fadeVariants: Variants = {
  initial: { opacity: 0 },
  animate: {
    opacity: 1,
    transition: { duration: 0.2, ease: 'easeOut' },
  },
  exit: {
    opacity: 0,
    transition: { duration: 0.12, ease: 'easeIn' },
  },
};

/** Slide-in from right — for detail panels opening */
export const slideRightVariants: Variants = {
  initial: { opacity: 0, x: 40 },
  animate: {
    opacity: 1,
    x: 0,
    transition: { duration: 0.25, ease: [0.16, 1, 0.3, 1] },
  },
  exit: {
    opacity: 0,
    x: 40,
    transition: { duration: 0.18, ease: [0.4, 0, 1, 1] },
  },
};

// ── Staggered List Variants ────────────────────────────────────────

/** Container that staggers children by `staggerDelay` seconds each */
export const staggerContainer: Variants = {
  animate: {
    transition: {
      staggerChildren: 0.04,
      delayChildren: 0.06,
    },
  },
  exit: {
    transition: {
      staggerChildren: 0.02,
      staggerDirection: -1,
    },
  },
};

/** Child item that fades + slides up on enter */
export const staggerItem: Variants = {
  initial: { opacity: 0, y: 16, scale: 0.97 },
  animate: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: {
      duration: 0.3,
      ease: [0.16, 1, 0.3, 1],
    },
  },
  exit: {
    opacity: 0,
    y: -8,
    scale: 0.97,
    transition: { duration: 0.15 },
  },
};

// ── Skeleton Morphing ──────────────────────────────────────────────

/** Skeleton shimmer → content reveal */
export const skeletonReveal: Variants = {
  initial: {
    opacity: 0.6,
    backgroundPosition: '200% 0',
  },
  animate: {
    opacity: 1,
    backgroundPosition: '0% 0',
    transition: { duration: 0.5, ease: 'easeOut' },
  },
};

// ── Micro-interactions ─────────────────────────────────────────────

/** Subtle scale bounce on hover — use with whileHover */
export const hoverScale = {
  scale: 1.02,
  transition: { duration: 0.2, ease: 'easeOut' },
};

/** Press-down feedback — use with whileTap */
export const tapScale = {
  scale: 0.97,
  transition: { duration: 0.1 },
};

// ── Animation Config ───────────────────────────────────────────────

/** Check if user prefers reduced motion */
export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined') return false;
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** Return no-animation variants when reduced motion is preferred */
export function motionSafe(variants: Variants): Variants {
  if (typeof window !== 'undefined' && prefersReducedMotion()) {
    return {
      initial: { opacity: 1 },
      animate: { opacity: 1, transition: { duration: 0 } },
      exit: { opacity: 1, transition: { duration: 0 } },
    };
  }
  return variants;
}
