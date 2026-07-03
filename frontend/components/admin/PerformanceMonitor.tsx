'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { FPSTracker, getQualityTier, type QualityTier } from '../../lib/performance/adaptiveQuality';

/**
 * FPS Performance Monitor — real-time rendering stats overlay.
 *
 * Toggle with Ctrl+Shift+P or via the gear icon in the topology panel.
 * Shows: FPS, quality tier, particle count, frame time, memory (if available).
 *
 * Part of AgentHub V5.1 P0 Performance Optimization
 */

interface FrameStats {
  fps: number;
  qualityTier: QualityTier;
  frameTime: number;
  particleCount: number;
  memoryMB?: number;
}

export default function PerformanceMonitor({
  visible = false,
  particleCount = 0,
}: {
  visible: boolean;
  particleCount?: number;
}): JSX.Element | null {
  const [stats, setStats] = useState<FrameStats>({
    fps: 60,
    qualityTier: getQualityTier(60),
    frameTime: 0,
    particleCount: 0,
  });
  const trackerRef = useRef<FPSTracker>(new FPSTracker(60));
  const rafRef = useRef<number>(0);
  const lastTimeRef = useRef<number>(performance.now());

  const updateStats = useCallback(() => {
    const tracker = trackerRef.current;
    tracker.tick();

    const now = performance.now();
    const frameTime = now - lastTimeRef.current;
    lastTimeRef.current = now;

    const currentFps = tracker.fps;
    const tier = getQualityTier(currentFps);

    // Get memory info if available
    let memoryMB: number | undefined;
    if ('memory' in performance && (performance as Performance & { memory?: { usedJSHeapSize: number } }).memory) {
      memoryMB = Math.round(
        ((performance as Performance & { memory: { usedJSHeapSize: number } }).memory.usedJSHeapSize /
          (1024 * 1024)) *
        10,
      ) / 10;
    }

    setStats({
      fps: currentFps,
      qualityTier: tier,
      frameTime: Math.round(frameTime * 10) / 10,
      particleCount,
      memoryMB,
    });

    rafRef.current = requestAnimationFrame(updateStats);
  }, [particleCount]);

  useEffect(() => {
    rafRef.current = requestAnimationFrame(updateStats);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [updateStats]);

  if (!visible) return null;

  const fpsColor =
    stats.fps >= 55 ? '#22c55e' : stats.fps >= 30 ? '#f59e0b' : '#ef4444';

  const tierBg: Record<string, string> = {
    Ultra: 'rgba(34,197,94,0.12)',
    High: 'rgba(59,130,246,0.12)',
    Medium: 'rgba(245,158,11,0.12)',
    Low: 'rgba(239,68,68,0.12)',
  };

  return (
    <div
      style={{
        position: 'absolute',
        top: 8,
        right: 8,
        zIndex: 50,
        background: 'rgba(17,24,39,0.92)',
        backdropFilter: 'blur(12px)',
        borderRadius: 12,
        padding: '10px 14px',
        color: '#e5e7eb',
        fontSize: 12,
        fontFamily: "'JetBrains Mono', 'Cascadia Code', monospace",
        lineHeight: 1.7,
        minWidth: 180,
        border: '1px solid rgba(255,255,255,0.08)',
        boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
      }}
    >
      {/* Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 6,
          paddingBottom: 6,
          borderBottom: '1px solid rgba(255,255,255,0.06)',
        }}
      >
        <span style={{ fontWeight: 700, color: '#f9fafb', fontSize: 13 }}>📊 Performance</span>
        <span
          style={{
            fontSize: 10,
            padding: '2px 6px',
            borderRadius: 4,
            background: tierBg[stats.qualityTier.label] || tierBg.Medium,
            color: fpsColor,
            fontWeight: 600,
          }}
        >
          {stats.qualityTier.label}
        </span>
      </div>

      {/* FPS */}
      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <span style={{ color: '#9ca3af' }}>FPS</span>
        <span style={{ color: fpsColor, fontWeight: 700, fontSize: 15 }}>{stats.fps}</span>
      </div>

      {/* Frame Time */}
      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <span style={{ color: '#9ca3af' }}>Frame</span>
        <span>{stats.frameTime}ms</span>
      </div>

      {/* Particles */}
      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <span style={{ color: '#9ca3af' }}>Particles</span>
        <span>
          {stats.particleCount}
          <span style={{ color: '#6b7280', marginLeft: 2 }}>
            / {stats.qualityTier.maxParticles}
          </span>
        </span>
      </div>

      {/* Memory */}
      {stats.memoryMB !== undefined && (
        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
          <span style={{ color: '#9ca3af' }}>Memory</span>
          <span>{stats.memoryMB}MB</span>
        </div>
      )}

      {/* Quality features */}
      <div
        style={{
          marginTop: 4,
          paddingTop: 4,
          borderTop: '1px solid rgba(255,255,255,0.06)',
          fontSize: 10,
          color: '#6b7280',
        }}
      >
        <div>
          Glow: {stats.qualityTier.glowEnabled ? '✅' : '❌'}
          {' · '}
          Flow: {stats.qualityTier.edgeFlowEnabled ? '✅' : '❌'}
        </div>
        <div>
          Emerge: {stats.qualityTier.emergenceEnabled ? '✅' : '❌'}
          {' · '}
          AA: {stats.qualityTier.antiAlias ? '✅' : '❌'}
        </div>
      </div>
    </div>
  );
}

/**
 * Hook to toggle performance monitor with keyboard shortcut.
 * Press Ctrl+Shift+P to toggle.
 */
export function usePerformanceMonitorToggle(): [boolean, () => void] {
  const [visible, setVisible] = useState(false);

  const toggle = useCallback(() => setVisible((v) => !v), []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && e.key === 'P') {
        e.preventDefault();
        toggle();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [toggle]);

  return [visible, toggle];
}
