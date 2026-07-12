/**
 * Adaptive Quality System — dynamically adjusts rendering complexity
 * based on measured FPS and device capabilities.
 *
 * Part of AgentHub V5.1 P0 Performance Optimization
 */

export interface QualityTier {
  /** Tier label for debugging */
  label: 'Ultra' | 'High' | 'Medium' | 'Low';
  /** Max particle count for topology visualization */
  maxParticles: number;
  /** Whether to render glow effects around agent nodes */
  glowEnabled: boolean;
  /** Whether to render data-flow particles along edges */
  edgeFlowEnabled: boolean;
  /** Whether to render anti-aliased circles */
  antiAlias: boolean;
  /** Whether to render emergence burst effects */
  emergenceEnabled: boolean;
  /** Whether to render background particle field */
  backgroundParticles: boolean;
  /** Physics simulation interval in ms (16 = 60fps, 33 = 30fps) */
  physicsInterval: number;
}

const TIER_ULTRA: QualityTier = {
  label: 'Ultra',
  maxParticles: 3000,
  glowEnabled: true,
  edgeFlowEnabled: true,
  antiAlias: true,
  emergenceEnabled: true,
  backgroundParticles: true,
  physicsInterval: 16,
};

const TIER_HIGH: QualityTier = {
  label: 'High',
  maxParticles: 1000,
  glowEnabled: true,
  edgeFlowEnabled: true,
  antiAlias: true,
  emergenceEnabled: true,
  backgroundParticles: false,
  physicsInterval: 16,
};

const TIER_MEDIUM: QualityTier = {
  label: 'Medium',
  maxParticles: 400,
  glowEnabled: false,
  edgeFlowEnabled: true,
  antiAlias: false,
  emergenceEnabled: false,
  backgroundParticles: false,
  physicsInterval: 33,
};

const TIER_LOW: QualityTier = {
  label: 'Low',
  maxParticles: 200,
  glowEnabled: false,
  edgeFlowEnabled: false,
  antiAlias: false,
  emergenceEnabled: false,
  backgroundParticles: false,
  physicsInterval: 50,
};

/**
 * Determine the appropriate quality tier based on current FPS and device memory.
 *
 * @param fps - Current measured frames per second (rolling average)
 * @param deviceMemory - Device RAM in GB (from navigator.deviceMemory, default 8)
 * @returns QualityTier with rendering parameters
 */
export function getQualityTier(fps: number, deviceMemory?: number): QualityTier {
  const mem = deviceMemory ?? (typeof navigator !== 'undefined' ? (navigator as Navigator & { deviceMemory?: number }).deviceMemory ?? 8 : 8);

  if (fps >= 55 && mem >= 4) {
    return TIER_ULTRA;
  }
  if (fps >= 40) {
    return TIER_HIGH;
  }
  if (fps >= 25) {
    return TIER_MEDIUM;
  }
  return TIER_LOW;
}

/** Rolling FPS tracker — call tick() each frame, read .fps for smoothed value */
export class FPSTracker {
  private timestamps: number[] = [];
  private _fps = 60;
  private readonly windowSize: number;

  constructor(windowSize = 60) {
    this.windowSize = windowSize;
  }

  tick(): void {
    const now = performance.now();
    this.timestamps.push(now);
    // Keep only the last windowSize entries
    while (this.timestamps.length > this.windowSize) {
      this.timestamps.shift();
    }
    if (this.timestamps.length >= 2) {
      const elapsed = this.timestamps[this.timestamps.length - 1] - this.timestamps[0];
      const count = this.timestamps.length - 1;
      this._fps = Math.round((count / elapsed) * 1000);
    }
  }

  get fps(): number {
    return this._fps;
  }
}

/**
 * Check if the browser supports WebGL (for PixiJS migration path).
 */
export function supportsWebGL(): boolean {
  try {
    const canvas = document.createElement('canvas');
    return !!(canvas.getContext('webgl2') || canvas.getContext('webgl'));
  } catch {
    return false;
  }
}

/**
 * Check if SharedArrayBuffer is available (requires COOP/COEP headers).
 */
export function supportsSharedArrayBuffer(): boolean {
  return typeof SharedArrayBuffer !== 'undefined';
}
