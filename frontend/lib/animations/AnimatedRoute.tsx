'use client';

// AnimatedRoute — wraps module content with framer-motion page transitions.
// Respects prefers-reduced-motion for accessibility (WCAG 2.1 §2.3.3).

import { type JSX, type ReactNode, useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { pageVariants, prefersReducedMotion } from './pageTransition';

export interface AnimatedRouteProps {
  /** Unique key that triggers exit/enter animation on change */
  routeKey: string;
  children: ReactNode;
  /** Override default page transition variants */
  variants?: typeof pageVariants;
  /** Disable animation entirely (e.g., for inline content swaps) */
  disableAnimation?: boolean;
}

export default function AnimatedRoute({
  routeKey,
  children,
  variants = pageVariants,
  disableAnimation = false,
}: AnimatedRouteProps): JSX.Element {
  const [reduceMotion, setReduceMotion] = useState(false);

  useEffect(() => {
    setReduceMotion(prefersReducedMotion());
  }, []);

  if (disableAnimation || reduceMotion) {
    return <div key={routeKey}>{children}</div>;
  }

  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.div
        key={routeKey}
        variants={variants}
        initial="initial"
        animate="animate"
        exit="exit"
        style={{ width: '100%', height: '100%' }}
      >
        {children}
      </motion.div>
    </AnimatePresence>
  );
}
