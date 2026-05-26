import type { JSX } from 'react';

interface PreviewSidebarProps {
  open: boolean;
  onClose: () => void;
  previewUrl: string;
}

export default function PreviewSidebar({ open, onClose, previewUrl }: PreviewSidebarProps): JSX.Element | null {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-warm-900/20">
      <div className="h-full w-[640px] bg-white p-6 shadow-sidebar">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-h3 text-warm-800">预览面板</h2>
          <button className="btn-ghost" onClick={onClose}>关闭</button>
        </div>
        {previewUrl ? (
          <iframe src={previewUrl} width="100%" height="760" className="rounded-xl border border-warm-150" />
        ) : (
          <div className="flex h-full items-center justify-center text-caption text-warm-400">暂无预览内容，请先完成任务或部署</div>
        )}
      </div>
    </div>
  );
}