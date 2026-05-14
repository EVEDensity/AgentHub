import { useState } from 'react';
import DiffBubble from './DiffBubble';

export default function GeneratedFilesPanel({ generated, onCommit }) {
  const [active, setActive] = useState(0);
  const [copied, setCopied] = useState(false);
  if (!generated?.fileDetails?.length) return null;
  const file = generated.fileDetails[active] || generated.fileDetails[0];

  async function copyContent() {
    await navigator.clipboard.writeText(file.content || '');
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  }

  return (
    <div className="mb-4 rounded-2xl border bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="font-semibold">CodeGen 已生成文件，等待确认提交</div>
          <div className="mt-1 text-sm text-slate-500">先检查文件内容和 Git Diff，再确认提交。</div>
        </div>
        <button className="rounded-xl bg-green-600 px-4 py-2 text-white hover:bg-green-700" onClick={onCommit}>确认提交</button>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {generated.fileDetails.map((item, index) => (
          <button key={item.path} className={`rounded-full px-3 py-1 text-sm ${index === active ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-700'}`} onClick={() => setActive(index)}>
            {item.path}
          </button>
        ))}
      </div>

      <div className="mt-4 overflow-hidden rounded-xl border">
        <div className="flex items-center justify-between border-b bg-slate-50 px-3 py-2 text-sm">
          <span className="font-mono text-slate-700">{file.path}</span>
          <button className="rounded-lg border bg-white px-3 py-1 hover:bg-slate-50" onClick={copyContent}>{copied ? '已复制' : '复制代码'}</button>
        </div>
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap bg-slate-950 p-4 text-sm leading-6 text-slate-100"><code>{file.content}</code></pre>
      </div>

      <div className="mt-4">
        <div className="mb-2 text-sm font-semibold text-slate-700">Git Diff</div>
        <DiffBubble value={generated.diff || '暂无 diff'} />
      </div>
    </div>
  );
}
