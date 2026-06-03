import { useEffect, useState, type JSX } from 'react';
import Head from 'next/head';
import MermaidChart from '../components/chat/MermaidChart';

interface ChartFile {
  name: string;
  filename: string;
  description: string;
  code: string;
}

/**
 * 图表预览页面 - 集中展示项目中的 Mermaid 图表
 * 
 * 访问路径: /charts
 */
export default function ChartsPage(): JSX.Element {
  const [charts, setCharts] = useState<ChartFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    async function loadCharts() {
      try {
        // 静态定义 3 个图表
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
            // 提取 ```mermaid ... ``` 代码块
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
        setError(e instanceof Error ? e.message : '加载失败');
      } finally {
        setLoading(false);
      }
    }
    void loadCharts();
  }, []);

  return (
    <>
      <Head>
        <title>图表说明 · AgentHub</title>
      </Head>
      <div className="min-h-screen bg-[#F8F7F4] py-8 px-4 sm:px-6 lg:px-8">
        <div className="max-w-5xl mx-auto">
          {/* 页面标题 */}
          <div className="mb-8">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h1 className="text-2xl font-semibold text-warm-900">图表说明</h1>
                <p className="mt-1 text-sm text-warm-500">
                  AgentHub 多智能体协作平台架构可视化
                </p>
              </div>
              <a
                href="/"
                className="btn-secondary"
              >
                ← 返回主页
              </a>
            </div>
          </div>

          {loading && (
            <div className="text-center py-12 text-warm-500">加载图表中...</div>
          )}

          {error && (
            <div className="rounded-2xl border border-red-200 bg-red-50 p-4 text-red-600">
              {error}
            </div>
          )}

          {!loading && !error && charts.length === 0 && (
            <div className="rounded-2xl border border-warm-150 bg-white p-8 text-center text-warm-500">
              暂无图表
            </div>
          )}

          {/* 图表列表 */}
          <div className="space-y-6">
            {charts.map((chart) => (
              <section
                key={chart.filename}
                className="rounded-2xl border border-warm-150 bg-white p-6"
              >
                <div className="mb-4">
                  <h2 className="text-lg font-semibold text-warm-900">
                    {chart.name}
                  </h2>
                  <p className="mt-1 text-sm text-warm-500">
                    {chart.description}
                  </p>
                </div>
                <MermaidChart
                  code={chart.code}
                  chartId={chart.filename.replace('.md', '')}
                />
              </section>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}
