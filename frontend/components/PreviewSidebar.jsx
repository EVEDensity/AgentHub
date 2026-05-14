export default function PreviewSidebar({ open, onClose, previewUrl }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/30">
      <div className="h-full w-[640px] bg-white p-5 shadow-2xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">预览面板</h2>
          <button className="rounded-lg border px-3 py-1 hover:bg-slate-50" onClick={onClose}>关闭</button>
        </div>
        {previewUrl ? <iframe src={previewUrl} width="100%" height="760" className="rounded-xl border" /> : <div className="flex h-full items-center justify-center text-slate-500">暂无预览内容，请先完成任务或部署</div>}
      </div>
    </div>
  );
}
