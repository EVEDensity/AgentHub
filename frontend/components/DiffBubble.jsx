import MonacoEditor from '@monaco-editor/react';

export default function DiffBubble({ value }) {
  return (
    <div className="mt-3 overflow-hidden rounded-lg border border-warm-200 bg-warm-900">
      <div className="border-b border-warm-700/30 px-3 py-2 text-xs font-semibold text-warm-200">代码 / Diff 预览</div>
      <MonacoEditor height="240px" language={value?.startsWith('diff') ? 'diff' : 'python'} theme="vs-dark" value={value || ''} options={{ readOnly: true, minimap: { enabled: false }, fontSize: 13 }} />
    </div>
  );
}