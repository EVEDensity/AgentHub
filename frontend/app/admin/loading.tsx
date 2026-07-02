import type { JSX } from 'react';

export default function AdminLoading(): JSX.Element {
  return (
    <div className="flex h-screen overflow-hidden bg-warm-50">
      {/* Sidebar skeleton */}
      <aside className="h-screen w-[254px] flex-none border-r border-warm-150 bg-[#F3F2F0]">
        <div className="sticky top-0 h-20 border-b border-warm-150 bg-[#ECEBE8] px-5 flex items-center">
          <div className="h-6 w-16 animate-pulse rounded bg-warm-200" />
        </div>
        <nav className="py-3 space-y-2 px-5">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="h-10 w-full animate-pulse rounded bg-warm-150" />
          ))}
        </nav>
      </aside>

      {/* Content skeleton */}
      <main className="flex-1 px-6 py-6 space-y-4">
        <div className="h-8 w-48 animate-pulse rounded bg-warm-150" />
        <div className="h-4 w-96 animate-pulse rounded bg-warm-100" />
        <div className="grid grid-cols-3 gap-4 mt-6">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-32 animate-pulse rounded-2xl bg-warm-100" />
          ))}
        </div>
        <div className="h-64 mt-4 animate-pulse rounded-2xl bg-warm-100" />
      </main>
    </div>
  );
}
