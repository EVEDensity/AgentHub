'use client';

// StaggerList — renders children with staggered entrance animations.
// Useful for card grids, table rows, and settings sections.

import { type JSX, type ReactNode, useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { staggerContainer, staggerItem, prefersReducedMotion } from './pageTransition';

export interface StaggerListProps {
  children: ReactNode;
  /** Delay between each child animation (seconds), default 0.04 */
  staggerDelay?: number;
  /** Delay before starting the first child (seconds), default 0.06 */
  initialDelay?: number;
  /** Override item variants */
  itemVariants?: typeof staggerItem;
  /** HTML tag for the container, default 'div' */
  as?: keyof JSX.IntrinsicElements;
  className?: string;
}

export default function StaggerList({
  children,
  staggerDelay = 0.04,
  initialDelay = 0.06,
  itemVariants = staggerItem,
  as: Tag = 'div',
  className,
}: StaggerListProps): JSX.Element {
  const [reduceMotion, setReduceMotion] = useState(false);

  useEffect(() => {
    setReduceMotion(prefersReducedMotion());
  }, []);

  if (reduceMotion) {
    return <Tag className={className}>{children}</Tag>;
  }

  const containerVariants = {
    animate: {
      transition: {
        staggerChildren: staggerDelay,
        delayChildren: initialDelay,
      },
    },
  };

  return (
    <motion.div
      variants={containerVariants}
      initial="initial"
      animate="animate"
      // @ts-expect-error dynamic tag
      as={Tag}
      className={className}
    >
      {Array.isArray(children)
        ? children.map((child, i) => (
            <motion.div key={i} variants={itemVariants}>
              {child}
            </motion.div>
          ))
        : children}
    </motion.div>
  );
}
