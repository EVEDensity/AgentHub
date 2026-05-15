export default function FidelityScore({ score = 0.95 }) {
  const percent = Math.round(score * 100);
  const color = score >= 0.8 ? 'bg-success-500' : score >= 0.7 ? 'bg-warning-500' : 'bg-danger-500';
  const label = score >= 0.7 ? '保真度达标' : '保真度不足';
  return (
    <div className="mt-2 flex items-center gap-2 text-caption">
      <span className="tag tag-warm">{label}</span>
      <div className="h-1.5 w-28 overflow-hidden rounded-full bg-warm-100">
        <div className={`h-full ${color}`} style={{ width: `${percent}%` }} />
      </div>
      <span className="text-warm-500">{Number(score).toFixed(2)}</span>
    </div>
  );
}