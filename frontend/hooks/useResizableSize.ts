import { useCallback, useEffect, useState } from 'react';

/**
 * useResizableSize
 * ────────────────
 * 管理一个受 localStorage 持久化的可调整尺寸。
 *
 * - 启动时从 localStorage 读取（key 不存在则用 default）
 * - 写入时自动 clamp 到 [min, max] 区间
 * - 跨标签页同步：监听 storage 事件，其他标签页改了值本标签页也会更新
 *
 * @param key      localStorage 中的唯一键
 * @param default_ 默认值（首次或 localStorage 损坏时使用）
 * @param min      下限（含）
 * @param max      上限（含）
 */
export function useResizableSize(
  key: string,
  default_: number,
  min: number,
  max: number,
): [number, (v: number) => void, () => void] {
  const safeDefault = clamp(default_, min, max);

  // Always start with the safe default for both SSR and client hydration.
  // Defer localStorage read to useEffect to avoid hydration mismatches
  // when the stored value differs from the default.
  const [size, setSizeState] = useState<number>(safeDefault);

  // Hydrate from localStorage on mount (client-only).
  useEffect(() => {
    if (typeof window === 'undefined') return;
    try {
      const raw = window.localStorage.getItem(key);
      if (raw != null) {
        const n = Number.parseFloat(raw);
        if (Number.isFinite(n)) {
          setSizeState(clamp(n, min, max));
        }
      }
    } catch {
      /* privacy mode — ignore */
    }
  }, [key, min, max]);

  // Cross-tab sync
  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key !== key || e.newValue == null) return;
      const n = Number.parseFloat(e.newValue);
      if (Number.isFinite(n)) setSizeState(clamp(n, min, max));
    }
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, [key, min, max]);

  const setSize = useCallback(
    (v: number) => {
      const c = clamp(v, min, max);
      setSizeState(c);
      try {
        window.localStorage.setItem(key, String(c));
      } catch {
        /* quota / privacy mode — ignore */
      }
    },
    [key, min, max],
  );

  const reset = useCallback(() => {
    setSize(safeDefault);
  }, [setSize, safeDefault]);

  return [size, setSize, reset];
}

export function clamp(v: number, min: number, max: number): number {
  if (v < min) return min;
  if (v > max) return max;
  return v;
}
