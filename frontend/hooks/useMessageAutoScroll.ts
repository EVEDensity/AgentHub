import { useEffect, useRef, type RefObject } from 'react';

/**
 * Message auto-scroll: session-switch scroll, streaming-throttled follow,
 * and near-bottom anchoring.
 *
 * Extracted from the page shell (R3/R4 hot-module thinning). Owns the
 * scroll-RAF and previous-count/session refs; the container element ref is
 * supplied by the caller so it can also be forwarded to the list component.
 */
export function useMessageAutoScroll(
  messages: unknown[],
  sessionId: string,
  containerRef: RefObject<HTMLElement | null>,
): void {
  const prevMessageCountRef = useRef(0);
  const prevSessionRef = useRef<string>(sessionId);
  const scrollRafRef = useRef<number>(0);
  const lastScrollTimeRef = useRef<number>(0);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const currentCount = messages.length;
    const isNewMessage = currentCount > prevMessageCountRef.current;
    const isSessionSwitch = sessionId !== prevSessionRef.current;
    prevMessageCountRef.current = currentCount;
    prevSessionRef.current = sessionId;

    // Session switch: immediate scroll to bottom
    if (isSessionSwitch) {
      if (scrollRafRef.current) cancelAnimationFrame(scrollRafRef.current);
      scrollRafRef.current = requestAnimationFrame(() => {
        container.scrollTo({ top: container.scrollHeight, behavior: 'auto' });
      });
      return;
    }

    // New message during streaming: throttle to ~30fps to avoid scroll jank.
    if (isNewMessage) {
      const now = performance.now();
      if (now - lastScrollTimeRef.current < 32) return;
      lastScrollTimeRef.current = now;

      if (scrollRafRef.current) cancelAnimationFrame(scrollRafRef.current);
      scrollRafRef.current = requestAnimationFrame(() => {
        container.scrollTo({ top: container.scrollHeight, behavior: 'auto' });
      });
      return;
    }

    // User is near the bottom: keep them anchored.
    const distanceToBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    if (distanceToBottom < 120) {
      container.scrollTo({ top: container.scrollHeight, behavior: 'auto' });
    }
  }, [messages, sessionId, containerRef]);
}