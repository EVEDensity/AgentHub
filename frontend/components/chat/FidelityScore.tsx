import type { JSX } from 'react';

interface FidelityScoreProps {
  score?: number;
  grade?: 'high' | 'warn' | 'low' | 'block';
  showDetail?: boolean;
}

const THRESHOLDS = {
  high: 0.85,
  warn: 0.70,
  low: 0.55,
} as const;

export function getFidelityGrade(score: number): 'high' | 'warn' | 'low' | 'block' {
  if (score >= THRESHOLDS.high) return 'high';
  if (score >= THRESHOLDS.warn) return 'warn';
  if (score >= THRESHOLDS.low) return 'low';
  return 'block';
}

export default function FidelityScore({ score = 0.95, grade, showDetail = false }: FidelityScoreProps): JSX.Element {
  const percent = Math.round(score * 100);
  const level = grade || getFidelityGrade(score);

  const config = {
    high:    { color: 'bg-success-500', text: 'text-success-600', label: '保真度达标', dot: 'bg-success-400' },
    warn:    { color: 'bg-warning-500', text: 'text-warning-600', label: '保真度偏低', dot: 'bg-warning-400' },
    low:     { color: 'bg-orange-500',  text: 'text-orange-600',  label: '保真度不足', dot: 'bg-orange-400' },
    block:   { color: 'bg-danger-500',  text: 'text-danger-600',  label: '保真度阻断', dot: 'bg-danger-400' },
  }[level];

  const badge = {
    high:    'tag-success',
    warn:    'tag-warning',
    low:     'tag-orange',
    block:   'tag-danger',
  }[level];

  return (
    <div className="mt-2 flex items-center gap-2 text-caption text-warm-500">
      <span className={`tag ${badge}`}>{config.label}</span>
      <div className="h-1.5 w-28 overflow-hidden rounded-full bg-warm-100">
        <div
          className={`h-full ${config.color} transition-all duration-500`}
          style={{ width: `${Math.min(percent, 100)}%` }}
        />
      </div>
      <span className={config.text}>{score.toFixed(2)}</span>
      {showDetail && level !== 'high' && (
        <span className="text-warm-400">
          {level === 'warn' ? '(建议核实)' : level === 'low' ? '(已拉取补充上下文)' : '(已阻断·需人工确认)'}
        </span>
      )}
    </div>
  );
}
