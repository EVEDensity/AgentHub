import type { JSX } from 'react';

interface FidelityScoreProps {
  score?: number;
}

export default function FidelityScore({ score = 0.95 }: FidelityScoreProps): JSX.Element {
  const percent = Math.round(score * 100);
  const color = score >= 0.8 ? 'bg-success-500' : score >= 0.7 ? 'bg-warning-500' : 'bg-danger-500';
  const label = score >= 0.7 ? '保真度达标' : '保真度不足';

  return (
    <div className="mt-2 flex items-center gap-2 text-caption text-warm-500">
      <span className="tag tag-warm">{label}</span>
      <div className="h-1.5 w-28 overflow-hidden rounded-full bg-warm-100">
        <div className={`h-full ${color} transition-all duration-300`} style={{ width: `${percent}%` }} />
      </div>
      <span className="text-warm-400">{Number(score).toFixed(2)}</span>
    </div>
  );
}