'use client';

import { Suspense, useEffect, useState, type JSX } from 'react';
import dynamic from 'next/dynamic';
import { useSearchParams } from 'next/navigation';

const AgentCanvas = dynamic(() => import('../../components/flow/AgentCanvas'), {
  ssr: false,
  loading: () => (
    <div className="flex h-screen items-center justify-center bg-warm-50 text-warm-500">
      正在加载 Agent 画布...
    </div>
  ),
});

/**
 * Inner content — uses useSearchParams() which requires a Suspense boundary
 * in Next.js 14 App Router (CSR bailout otherwise).
 */
function CanvasContent(): JSX.Element {
  const searchParams = useSearchParams();
  const [workflowId, setWorkflowId] = useState<number | undefined>(undefined);
  const [isNew, setIsNew] = useState(false);

  useEffect(() => {
    const idParam = searchParams?.get('id');
    const newParam = searchParams?.get('new');

    if (idParam) {
      const id = parseInt(idParam, 10);
      if (!isNaN(id) && id > 0) {
        setWorkflowId(id);
        return;
      }
    }

    if (newParam === 'true') {
      setIsNew(true);
    }
  }, [searchParams]);

  return (
    <AgentCanvas
      workflowId={workflowId}
      initialData={undefined}
    />
  );
}

export default function CanvasPage(): JSX.Element {
  return (
    <Suspense
      fallback={
        <div className="flex h-screen items-center justify-center bg-warm-50 text-warm-500">
          正在加载 Agent 画布...
        </div>
      }
    >
      <CanvasContent />
    </Suspense>
  );
}
