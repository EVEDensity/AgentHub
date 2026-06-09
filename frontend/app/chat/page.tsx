'use client';

import { useEffect, type JSX } from 'react';
import { useRouter } from 'next/navigation';

/**
 * App Router chat page — redirects to the Pages Router chat at /.
 * The Pages Router (pages/index.tsx) serves the full chat experience.
 */
export default function ChatPage(): JSX.Element {
  const router = useRouter();

  useEffect(() => {
    // Redirect to root, which is handled by Pages Router
    window.location.href = '/';
  }, [router]);

  return (
    <div className="flex h-screen items-center justify-center bg-warm-50">
      <div className="flex flex-col items-center gap-4">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
        <span className="text-sm text-warm-500">正在跳转到聊天页面...</span>
      </div>
    </div>
  );
}
