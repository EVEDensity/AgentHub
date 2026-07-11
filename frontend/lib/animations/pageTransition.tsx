'use client';

import { type JSX, type ReactNode } from 'react';
import { motion, AnimatePresence, type Variants } from 'framer-motion';

/**
 * Page transition animation primitives.
 *
 * Reusable framer-motion wrappers for consistent page transitions,
 * staggered list entries, and skeleton-to-content morphing across
 * AgentHub's 22 admin modules.
 *
 * Part of AgentHub V5.1 P2-4 — Page Transition Animations
 *
 * Uses the design token easing curves from tokens/index.ts:
 *   - ease:  [0.16, 1, 0.3, 1]  (default exit)
 *   - inOut: [0.4, 0, 0.2, 1]   (enter+exit combined)
 *   - spring: { stiffness: 300, damping: 30 }
 */

// ── Animation Variants ──────────────────────────────────────────

/** Standard page enter/exit transition for AnimatePresence */
export const pageVariants: Variants = {
  initial: {
    opacity: 0,
    y: 12,
    scale: 0.995,
    filter: 'blur(2px)',
  },
  animate: {
    opacity: 1,
    y: 0,
    scale: 1,
    filter: 'blur(0px)',
    transition: {
      duration: 0.25,
      ease: [0.16, 1, 0.3, 1],
      staggerChildren: 0.04,
      delayChildren: 0.05,
    },
  },
  exit: {
    opacity: 0,
    y: -8,
    scale: 0.995,
    filter: 'blur(2px)',
    transition: {
      duration: 0.18,
      ease: [0.4, 0, 0.2, 1],
    },
  },
};

/** Slide-in from right (e.g., detail panels, drawers) */
export const slideRightVariants: Variants = {
  initial: { opacity: 0, x: 40 },
  animate: {
    opacity: 1,
    x: 0,
    transition: { duration: 0.3, ease: [0.16, 1, 0.3, 1] },
  },
  exit: {
    opacity: 0,
    x: 40,
    transition: { duration: 0.2, ease: [0.4, 0, 0.2, 1] },
  },
};

/** Fade only (for modals, overlays) */
export const fadeVariants: Variants = {
  initial: { opacity: 0 },
  animate: {
    opacity: 1,
    transition: { duration: 0.2, ease: 'easeOut' },
  },
  exit: {
    opacity: 0,
    transition: { duration: 0.15, ease: 'easeIn' },
  },
};

/** Scale up (for popovers, tooltips, dropdowns) */
export const scaleUpVariants: Variants = {
  initial: { opacity: 0, scale: 0.92, y: -4 },
  animate: {
    opacity: 1,
    scale: 1,
    y: 0,
    transition: { duration: 0.2, ease: [0.16, 1, 0.3, 1] },
  },
  exit: {
    opacity: 0,
    scale: 0.92,
    y: -4,
    transition: { duration: 0.15, ease: 'easeIn' },
  },
};

/** Staggered children: each child fades in + slides up slightly, staggered */
export const staggerContainerVariants: Variants = {
  initial: {},
  animate: {
    transition: {
      staggerChildren: 0.05,
      delayChildren: 0.03,
    },
  },
  exit: {
    transition: { staggerChildren: 0.03, staggerDirection: -1 },
  },
};

export const staggerChildVariants: Variants = {
  initial: { opacity: 0, y: 8 },
  animate: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.3, ease: [0.16, 1, 0.3, 1] },
  },
  exit: {
    opacity: 0,
    y: -4,
    transition: { duration: 0.15, ease: 'easeIn' },
  },
};

// ── Skeleton Morphing ────────────────────────────────────────────

/** Morphing variants for skeleton → content transitions */
export const skeletonMorphVariants: Variants = {
  skeleton: {
    opacity: 0.6,
    backgroundImage: 'linear-gradient(90deg, #e5e7eb 25%, #f3f4f6 50%, #e5e7eb 75%)',
    backgroundSize: '200% 100%',
    transition: { duration: 0.3 },
  },
  content: {
    opacity: 1,
    backgroundImage: 'linear-gradient(90deg, transparent 0%, transparent 100%)',
    backgroundSize: '200% 100%',
    transition: { duration: 0.4, ease: [0.16, 1, 0.3, 1] },
  },
};

// ── Components ───────────────────────────────────────────────────

interface PageTransitionProps {
  children: ReactNode;
  /** Unique key for AnimatePresence tracking (typically the route path) */
  transitionKey: string;
  /** Animation variant, defaults to pageVariants */
  variants?: Variants;
  /** Animation mode for AnimatePresence */
  mode?: 'wait' | 'sync' | 'popLayout';
}

/**
 * Page-level transition wrapper using AnimatePresence.
 *
 * @example
 *   <PageTransition transitionKey={pathname}>
 *     <main>{children}</main>
 *   </PageTransition>
 */
export function PageTransition({
  children,
  transitionKey,
  variants = pageVariants,
  mode = 'wait',
}: PageTransitionProps): JSX.Element {
  return (
    <AnimatePresence mode={mode} initial={false}>
      <motion.div
        key={transitionKey}
        variants={variants}
        initial="initial"
        animate="animate"
        exit="exit"
        style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}
      >
        {children}
      </motion.div>
    </AnimatePresence>
  );
}

interface StaggerListProps {
  children: ReactNode;
  /** Items-per-row hint for grid layouts (affects stagger order feel) */
  columns?: number;
  className?: string;
}

/**
 * Wraps a list of items with staggered entry animations.
 * Each direct child will fade in one after another.
 *
 * @example
 *   <StaggerList>
 *     {cards.map(c => <Card key={c.id}>{c.content}</Card>)}
 *   </StaggerList>
 */
export function StaggerList({
  children,
  className,
}: StaggerListProps): JSX.Element {
  return (
    <motion.div
      variants={staggerContainerVariants}
      initial="initial"
      animate="animate"
      exit="exit"
      className={className}
    >
      {children}
    </motion.div>
  );
}

/**
 * A single stagger item — wraps content with individual animation.
 */
export function StaggerItem({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}): JSX.Element {
  return (
    <motion.div variants={staggerChildVariants} className={className}>
      {children}
    </motion.div>
  );
}

interface SkeletonMorphProps {
  children: ReactNode;
  /** Whether content is loaded */
  loaded: boolean;
  /** Skeleton placeholder to show while loading */
  skeleton: ReactNode;
  className?: string;
}

/**
 * Morphs from a skeleton placeholder to real content when loaded.
 *
 * @example
 *   <SkeletonMorph loaded={!!data} skeleton={<div className="skeleton skeleton-text" />}>
 *     <p>{data?.text}</p>
 *   </SkeletonMorph>
 */
export function SkeletonMorph({
  children,
  loaded,
  skeleton,
  className,
}: SkeletonMorphProps): JSX.Element {
  return (
    <motion.div
      variants={skeletonMorphVariants}
      animate={loaded ? 'content' : 'skeleton'}
      className={className}
      style={{ position: 'relative' }}
    >
      {loaded ? children : skeleton}
    </motion.div>
  );
}
