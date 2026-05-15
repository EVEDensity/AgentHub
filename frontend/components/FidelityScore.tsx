import type { JSX } from 'react';

interface FidelityScoreProps {
  score?: number;
}

export default function FidelityScore({ score = 0.95 }: FidelityScoreProps): JSX.Element {
  const percent = Math.round(score * 100);
  const color = score >= 0.8 ? 'bg-green-500' : score >= 0.7 ? 'bg-yellow-500' : 'bg-red-500';
  const label = score >= 0.7 ? '保真度达标' : '保真度不足';

  return (
    <div className="mt-2 flex items-center gap-2 text-sm text-slate-500">
      <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{label}</span>
      <div className="h-1.5 w-28 overflow-hidden rounded-full bg-slate-200">
        <div className={`h-full ${color}`} style={{ width: `${percent}%` }} />
      </div>
      <span className="text-slate-500">{Number(score).toFixed(2)}</span>
    </div>
  );
}