export default function FidelityScore({ score = 0.95 }) {
  const percent = Math.round(score * 100);
  const color = score >= 0.8 ? 'bg-green-500' : score >= 0.7 ? 'bg-orange-500' : 'bg-red-500';
  const label = score >= 0.7 ? '保真度达标' : '保真度不足';
  return (
    <div className="mt-2 flex items-center gap-2 text-xs text-slate-500">
      <span className="rounded-full border px-2 py-1">{label}</span>
      <div className="h-2 w-28 overflow-hidden rounded-full bg-slate-200">
        <div className={`h-full ${color}`} style={{ width: `${percent}%` }} />
      </div>
      <span>{Number(score).toFixed(2)}</span>
    </div>
  );
}
