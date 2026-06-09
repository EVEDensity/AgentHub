'use client';

import { useEffect, useState, type JSX } from 'react';
import MermaidChart from '../../components/chat/MermaidChart';

interface ChartFile {
  name: string;
  filename: string;
  description: string;
  code: string;
}

export default function ChartsPage(): JSX.Element {
  const [charts, setCharts] = useState<ChartFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    async function loadCharts() {
      try {
        const chartDefs = [
          { name: '核心架构三维图', filename: '01-core-architecture.md', description: '感知→认知→行动的层级关系，角色对推理的约束，以及记忆作为贯穿全流程的"海马体"' },
          { name: 'OODA 工作流循环图', filename: '02-ooda-workflow.md', description: 'chat() 方法内部严格的 观测→判断→执行 时序，突出每一步的原子性与可追踪性' },
          { name: '演化路径图', filename: '03-evolution-path.md', description: '从当前最简骨架到生产级 Agent 的三个关键跨越方向：记忆增强、推理升级、行动赋能' },
        ];

        const loaded: ChartFile[] = [];
        for (const def of chartDefs) {
          try {
            const res = await fetch(`/charts/${def.filename}`);
            if (!res.ok) continue;
            const text = await res.text();
            const match = /```mermaid\s*([\s\S]*?)```/.exec(text);
            if (match) {
              loaded.push({
                name: def.name,
                filename: def.filename,
                description: def.description,
                code: match[1].trim(),
              });
            }
          } catch (e) {
            console.error(`Failed to load ${def.filename}:`, e);
          }
        }
        setCharts(loaded);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load charts');
      } finally {
        setLoading(false);
      }
    }
    void loadCharts();
  }, []);

  return (
    <div className="min-h-screen bg-warm-50">
      <div className="max-w-5xl mx-auto px-6 py-10">
        <header className="mb-10">
          <h1 className="text-[34px] font-semibold text-warm-900">Mermaid 图表预览</h1>
          <p className="mt-2 text-sm text-warm-500">AgentHub 核心架构与工作流可视化</p>
        </header>

        {loading && (
          <div className="flex justify-center py-20">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
          </div>
        )}

        {error && (
          <div className="rounded-xl bg-red-50 border border-red-200 p-4 text-sm text-red-700">
            {error}
          </div>
        )}

        {!loading && !error && charts.length === 0 && (
          <div className="text-center py-20 text-warm-400">未找到图表文件</div>
        )}

        {!loading && !error && charts.map((chart) => (
          <section key={chart.filename} className="mb-10 bg-white rounded-2xl border border-warm-200 p-6">
            <h2 className="text-xl font-semibold text-warm-800 mb-1">{chart.name}</h2>
            <p className="text-sm text-warm-500 mb-4">{chart.description}</p>
            <MermaidChart code={chart.code} />
          </section>
        ))}
      </div>
    </div>
  );
}
