'use client';

import type { JSX } from 'react';

interface ErrorProps {
  error: Error;
  reset: () => void;
}

export default function AdminError({ error, reset }: ErrorProps): JSX.Element {
  return (
    <div className="flex flex-col items-center justify-center h-96 gap-4">
      <span className="material-symbols-outlined text-5xl text-warm-300">error_outline</span>
      <h2 className="text-xl font-semibold text-warm-700">页面加载失败</h2>
      <p className="text-sm text-warm-500 max-w-md text-center">
        {error.message || '发生未知错误，请检查后端服务是否正常运行。'}
      </p>
      <button
        onClick={reset}
        className="mt-2 px-5 py-2 rounded-lg bg-primary-500 text-white text-sm hover:bg-primary-600 transition-colors"
      >
        重试
      </button>
    </div>
  );
}
