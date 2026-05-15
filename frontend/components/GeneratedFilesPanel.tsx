import { useState, type JSX } from 'react';
import DiffBubble from './DiffBubble';
import type { GeneratedData } from '../types';

interface GeneratedFilesPanelProps {
  generated: GeneratedData;
  onCommit: () => void | Promise<void>;
}

export default function GeneratedFilesPanel({ generated, onCommit }: GeneratedFilesPanelProps): JSX.Element | null {
  const [active, setActive] = useState<number>(0);
  const [copied, setCopied] = useState<boolean>(false);

  if (!generated?.fileDetails?.length) return null;

  const file = generated.fileDetails[active] || generated.fileDetails[0];

  async function copyContent(): Promise<void> {
    await navigator.clipboard.writeText(file.content || '');
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  }

  return (
    <div className="mb-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="text-lg font-semibold">CodeGen 已生成文件，等待确认提交</div>
          <div className="mt-1 text-sm text-slate-500">先检查文件内容和 Git Diff，再确认提交。</div>
        </div>
        <button className="rounded-xl bg-green-600 px-4 py-2 text-white hover:bg-green-700" onClick={onCommit}>确认提交</button>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {generated.fileDetails.map((item, index) => (
          <button key={item.path}
            className={`rounded-full px-3 py-1 text-sm ${index === active ? 'bg-blue-100 text-blue-700' : 'bg-slate-100 text-slate-600'}`}
            onClick={() => setActive(index)}>
            {item.path}
          </button>
        ))}
      </div>

      <div className="mt-4 overflow-hidden rounded-lg border border-slate-200">
        <div className="flex items-center justify-between border-b border-slate-200 bg-slate-50 px-3 py-2 text-sm">
          <span className="font-mono text-slate-700">{file.path}</span>
          <button className="rounded-lg border bg-white px-3 py-1 text-xs text-slate-600 hover:bg-slate-50" onClick={copyContent}>
            {copied ? '已复制' : '复制代码'}
          </button>
        </div>
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap bg-slate-900 p-4 text-sm leading-6 text-slate-100"><code>{file.content}</code></pre>
      </div>

      <div className="mt-4">
        <div className="mb-2 text-lg font-semibold text-slate-700">Git Diff</div>
        <DiffBubble value={generated.diff || '暂无 diff'} />
      </div>
    </div>
  );
}