import { useState, type JSX } from 'react';
import DiffBubble from '../chat/DiffBubble';
import type { GeneratedData } from '../../types';

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
    <div className="card mb-4 p-5">
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="text-h3 text-warm-800">CodeGen 已生成文件，等待确认提交</div>
          <div className="mt-1 text-caption text-warm-500">先检查文件内容和 Git Diff，再确认提交。</div>
        </div>
        <button className="btn-primary" onClick={onCommit}>确认提交</button>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {generated.fileDetails.map((item, index) => (
          <button key={item.path}
            className={`rounded-lg px-3 py-1.5 text-caption transition-all ${
              index === active ? 'bg-primary-50 text-primary-600' : 'bg-warm-100 text-warm-600 hover:bg-warm-150'
            }`}
            onClick={() => setActive(index)}>
            {item.path}
          </button>
        ))}
      </div>

      <div className="mt-4 overflow-hidden rounded-xl border border-warm-150">
        <div className="flex items-center justify-between border-b border-warm-150 bg-warm-50 px-4 py-2.5">
          <span className="font-mono text-caption text-warm-700">{file.path}</span>
          <button className="btn-ghost text-caption" onClick={copyContent}>
            {copied ? '已复制' : '复制代码'}
          </button>
        </div>
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap bg-warm-900 p-4 text-sm leading-6 text-warm-100"><code>{file.content}</code></pre>
      </div>

      <div className="mt-5">
        <div className="mb-3 text-h4 text-warm-700">Git Diff</div>
        <DiffBubble value={generated.diff || '暂无 diff'} />
      </div>
    </div>
  );
}