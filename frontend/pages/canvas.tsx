import dynamic from 'next/dynamic';
import type { JSX } from 'react';

const AgentCanvas = dynamic(() => import('../components/AgentCanvas'), {
  ssr: false,
  loading: () => (
    <div className="flex h-screen items-center justify-center bg-warm-50 text-warm-500">
      正在加载 Agent 画布...
    </div>
  ),
});

export default function CanvasPage(): JSX.Element {
  return <AgentCanvas />;
}
