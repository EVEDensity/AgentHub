/**
 * Focus Trap & Keyboard Navigation Utilities
 *
 * WCAG 2.1 AA compliant hooks for:
 * - Focus trapping within modals/dialogs (WCAG 2.4.3)
 * - Focus restoration on close (WCAG 2.4.3)
 * - Keyboard list navigation (↑↓ for listboxes, ←→ for tabs)
 * - Skip-to-content detection
 *
 * Part of AgentHub V5.1 §6.4 — WCAG 2.1 AA Accessibility
 */

import { useEffect, useRef, useCallback } from 'react';

// ── Focus Trap ──────────────────────────────────────────────────

/** CSS selectors for focusable elements */
const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
  '[contenteditable="true"]',
].join(', ');

/**
 * Trap keyboard focus within a container element.
 *
 * Used in modals, drawers, and dialogs to meet WCAG 2.4.3 (Focus Order).
 * On Escape, calls `onEscape` (typically closes the modal).
 *
 * @param containerRef — React ref to the focus-trapped container
 * @param active — Whether the trap is active (e.g., modal is open)
 * @param onEscape — Called when Escape is pressed
 */
export function useFocusTrap(
  containerRef: React.RefObject<HTMLElement | null>,
  active: boolean,
  onEscape?: () => void,
): void {
  const previousFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!active || !containerRef.current) return;

    // Save previously focused element for restoration
    previousFocusRef.current = document.activeElement as HTMLElement;

    const container = containerRef.current;

    // Find all focusable elements
    const getFocusables = (): HTMLElement[] => {
      if (!container) return [];
      const nodes = container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR);
      return Array.from(nodes).filter(
        (el) => el.offsetParent !== null && !el.hasAttribute('disabled'),
      );
    };

    // Auto-focus first focusable element after a tick
    const focusTimer = setTimeout(() => {
      const focusables = getFocusables();
      if (focusables.length > 0) {
        focusables[0].focus();
      }
    }, 50);

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && onEscape) {
        e.preventDefault();
        onEscape();
        return;
      }

      if (e.key !== 'Tab') return;

      const focusables = getFocusables();
      if (focusables.length === 0) {
        e.preventDefault();
        return;
      }

      const first = focusables[0];
      const last = focusables[focusables.length - 1];

      if (e.shiftKey) {
        // Shift+Tab: wrap to last
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else {
        // Tab: wrap to first
        if (document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    document.body.style.overflow = 'hidden';

    return () => {
      clearTimeout(focusTimer);
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = '';

      // Restore focus to previously focused element
      if (previousFocusRef.current && typeof previousFocusRef.current.focus === 'function') {
        previousFocusRef.current.focus();
      }
    };
  }, [active, containerRef, onEscape]);
}

// ── List Keyboard Navigation ─────────────────────────────────────

/**
 * Keyboard navigation for lists (e.g., dropdowns, autocomplete results).
 * Arrow keys move focus between items, Enter/Space selects.
 *
 * @param containerRef — the list container element
 * @param itemSelector — CSS selector for list items within the container
 * @param onSelect — called with the selected element's index
 * @param active — whether keyboard nav is active
 */
export function useListKeyboardNav(
  containerRef: React.RefObject<HTMLElement | null>,
  itemSelector: string,
  onSelect: (index: number) => void,
  active: boolean = true,
): void {
  useEffect(() => {
    if (!active || !containerRef.current) return;

    const container = containerRef.current;

    const handleKeyDown = (e: KeyboardEvent) => {
      const items = Array.from(
        container.querySelectorAll<HTMLElement>(itemSelector),
      ).filter((el) => el.offsetParent !== null);

      if (items.length === 0) return;

      const currentIndex = items.findIndex((el) => el === document.activeElement);

      switch (e.key) {
        case 'ArrowDown': {
          e.preventDefault();
          const next = currentIndex < items.length - 1 ? currentIndex + 1 : 0;
          items[next].focus();
          break;
        }
        case 'ArrowUp': {
          e.preventDefault();
          const prev = currentIndex > 0 ? currentIndex - 1 : items.length - 1;
          items[prev].focus();
          break;
        }
        case 'Enter':
        case ' ': {
          e.preventDefault();
          if (currentIndex >= 0) {
            onSelect(currentIndex);
          }
          break;
        }
        case 'Home': {
          e.preventDefault();
          items[0].focus();
          break;
        }
        case 'End': {
          e.preventDefault();
          items[items.length - 1].focus();
          break;
        }
      }
    };

    container.addEventListener('keydown', handleKeyDown);
    return () => container.removeEventListener('keydown', handleKeyDown);
  }, [active, containerRef, itemSelector, onSelect]);
}

// ── Tab List Keyboard Navigation ──────────────────────────────────

/**
 * Keyboard navigation for tab lists (WCAG 2.1 Tab Panel pattern).
 * Left/Right arrows move between tabs.
 *
 * @param tabsRef — ref to the tab list container
 * @param tabSelector — CSS selector for individual tabs
 * @param onSelect — called with the tab index when Enter/Space is pressed
 * @param activeIndex — currently selected tab index
 */
export function useTabKeyboardNav(
  tabsRef: React.RefObject<HTMLElement | null>,
  tabSelector: string,
  onSelect: (index: number) => void,
  activeIndex: number = 0,
): void {
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      const container = tabsRef.current;
      if (!container) return;

      const tabs = Array.from(
        container.querySelectorAll<HTMLElement>(tabSelector),
      ).filter((el) => el.offsetParent !== null);

      if (tabs.length === 0) return;

      let nextIndex = activeIndex;

      switch (e.key) {
        case 'ArrowRight':
        case 'ArrowDown':
          e.preventDefault();
          nextIndex = activeIndex < tabs.length - 1 ? activeIndex + 1 : 0;
          tabs[nextIndex].focus();
          break;
        case 'ArrowLeft':
        case 'ArrowUp':
          e.preventDefault();
          nextIndex = activeIndex > 0 ? activeIndex - 1 : tabs.length - 1;
          tabs[nextIndex].focus();
          break;
        case 'Enter':
        case ' ':
          e.preventDefault();
          onSelect(activeIndex);
          break;
        case 'Home':
          e.preventDefault();
          tabs[0].focus();
          break;
        case 'End':
          e.preventDefault();
          tabs[tabs.length - 1].focus();
          break;
      }
    },
    [tabsRef, tabSelector, onSelect, activeIndex],
  );

  useEffect(() => {
    const container = tabsRef.current;
    if (!container) return;

    container.addEventListener('keydown', handleKeyDown);
    return () => container.removeEventListener('keydown', handleKeyDown);
  }, [tabsRef, handleKeyDown]);
}

// ── Skip-to-content ─────────────────────────────────────────────

/**
 * Returns true if the current page load was triggered by a
 * "skip to content" link click. Components can use this to
 * focus their main heading on mount.
 */
export function useSkipToContent(ref: React.RefObject<HTMLElement | null>): void {
  useEffect(() => {
    // If the URL hash is #main-content, focus the element
    if (typeof window !== 'undefined' && window.location.hash === '#main-content') {
      const el = ref.current || document.getElementById('main-content');
      if (el) {
        el.focus();
        // Clear the hash so back-navigation doesn't re-trigger
        if (window.history && window.history.replaceState) {
          window.history.replaceState(null, '', window.location.pathname + window.location.search);
        }
      }
    }
  }, [ref]);
}
