import type { JSX } from 'react';

interface SimpleChartProps {
  data: Array<{ label: string; value: number; color?: string }>;
  type?: 'bar' | 'donut';
  maxValue?: number;
  height?: number;
  showLabels?: boolean;
}

const PIE_COLORS = [
  '#10b981', '#3b82f6', '#f59e0b', '#ef4444', '#8b5cf6',
  '#06b6d4', '#f97316', '#ec4899', '#84cc16', '#6366f1',
];

export default function SimpleChart({
  data,
  type = 'bar',
  maxValue,
  height = 160,
  showLabels = true,
}: SimpleChartProps): JSX.Element {
  if (!data.length) {
    return <div className="flex items-center justify-center text-xs text-warm-400" style={{ height }}>暂无数据</div>;
  }

  if (type === 'donut') {
    return <DonutChart data={data} height={height} showLabels={showLabels} />;
  }

  return <BarChart data={data} maxValue={maxValue} height={height} showLabels={showLabels} />;
}

function BarChart({
  data,
  maxValue,
  height,
  showLabels,
}: { data: SimpleChartProps['data']; maxValue?: number; height: number; showLabels: boolean }): JSX.Element {
  const max = maxValue || Math.max(...data.map((d) => d.value), 1);
  const barAreaHeight = height - (showLabels ? 24 : 0);
  const barWidth = Math.max(8, Math.min(40, 100 / data.length - 4));

  return (
    <div style={{ height }}>
      <div className="flex items-end gap-1" style={{ height: barAreaHeight }}>
        {data.map((d, i) => {
          const h = Math.max(4, (d.value / max) * barAreaHeight);
          return (
            <div
              key={i}
              className="flex flex-1 flex-col items-center justify-end"
              title={`${d.label}: ${d.value}`}
            >
              <div
                className="w-full rounded-t transition-all duration-300"
                style={{
                  height: `${h}px`,
                  backgroundColor: d.color || PIE_COLORS[i % PIE_COLORS.length],
                  minWidth: `${barWidth}px`,
                  maxWidth: '48px',
                }}
              />
            </div>
          );
        })}
      </div>
      {showLabels && (
        <div className="mt-1 flex gap-1">
          {data.map((d, i) => (
            <div key={i} className="flex-1 truncate text-center text-[10px] text-warm-500" style={{ maxWidth: '48px' }}>
              {d.label.length > 6 ? d.label.slice(0, 5) + '…' : d.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function DonutChart({
  data,
  height,
  showLabels,
}: { data: SimpleChartProps['data']; height: number; showLabels: boolean }): JSX.Element {
  const total = data.reduce((s, d) => s + d.value, 0) || 1;
  const cx = height / 2;
  const cy = height / 2;
  const radius = height * 0.38;
  const strokeWidth = height * 0.18;

  // Build SVG arc paths
  let cumulativeAngle = -90;
  const slices: Array<{ path: string; color: string; label: string; percent: number }> = [];

  for (let i = 0; i < data.length; i++) {
    const d = data[i];
    const sliceAngle = (d.value / total) * 360;
    const startAngle = cumulativeAngle;
    const endAngle = cumulativeAngle + sliceAngle;

    const startRad = (startAngle * Math.PI) / 180;
    const endRad = (endAngle * Math.PI) / 180;

    const x1 = cx + radius * Math.cos(startRad);
    const y1 = cy + radius * Math.sin(startRad);
    const x2 = cx + radius * Math.cos(endRad);
    const y2 = cy + radius * Math.sin(endRad);

    const largeArc = sliceAngle > 180 ? 1 : 0;

    slices.push({
      path: `M ${cx} ${cy} L ${x1} ${y1} A ${radius} ${radius} 0 ${largeArc} 1 ${x2} ${y2} Z`,
      color: d.color || PIE_COLORS[i % PIE_COLORS.length],
      label: d.label,
      percent: Math.round((d.value / total) * 100),
    });

    cumulativeAngle = endAngle;
  }

  return (
    <div className="flex gap-3" style={{ height }}>
      <svg width={height} height={height} viewBox={`0 0 ${height} ${height}`}>
        {slices.map((s, i) => (
          <path key={i} d={s.path} fill={s.color} stroke="white" strokeWidth="1.5" />
        ))}
        <circle cx={cx} cy={cy} r={radius - strokeWidth} fill="white" />
        <text x={cx} y={cy - 6} textAnchor="middle" className="fill-warm-700 text-[16px] font-bold" style={{ fontSize: '14px' }}>
          {total}
        </text>
        <text x={cx} y={cy + 10} textAnchor="middle" className="fill-warm-400" style={{ fontSize: '9px' }}>
          总计
        </text>
      </svg>
      {showLabels && (
        <div className="flex flex-col justify-center gap-1 overflow-auto">
          {slices.map((s, i) => (
            <div key={i} className="flex items-center gap-1.5 text-xs">
              <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: s.color }} />
              <span className="truncate text-warm-600">{s.label}</span>
              <span className="text-warm-400">{s.percent}%</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
