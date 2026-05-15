import MonacoEditor from '@monaco-editor/react';

export default function DiffBubble({ value }) {
  const language = value?.startsWith('diff') ? 'diff' : 'python';

  return (
    <div className="mt-2 overflow-hidden rounded-xl border border-warm-200/80 bg-warm-900 shadow-warm-md">
      <div className="flex items-center justify-between border-b border-warm-700/40 bg-warm-800/70 px-4 py-2.5">
        <div className="text-xs font-semibold tracking-wide text-warm-100">代码 / Diff 预览</div>
        <div className="tag tag-warm border border-warm-600/60 bg-warm-700/40 text-[11px] text-warm-200">{language.toUpperCase()}</div>
      </div>
      <MonacoEditor
        height="360px"
        language={language}
        theme="vs-dark"
        value={value || ''}
        options={{
          readOnly: true,
          minimap: { enabled: false },
          fontSize: 14,
          lineHeight: 22,
          scrollBeyondLastLine: false,
          wordWrap: 'on',
          padding: { top: 14, bottom: 14 },
          smoothScrolling: true,
        }}
      />
    </div>
  );
}
