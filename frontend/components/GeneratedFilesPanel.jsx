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
    <div className="card mb-4 p-4">
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="text-h4">CodeGen 已生成文件，等待确认提交</div>
          <div className="mt-1 text-caption">先检查文件内容和 Git Diff，再确认提交。</div>
        </div>
        <button className="btn-primary bg-success-500 hover:bg-success-600" onClick={onCommit}>确认提交</button>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {generated.fileDetails.map((item, index) => (
          <button key={item.path} className={`tag ${index === active ? 'tag-blue' : 'tag-warm'}`} onClick={() => setActive(index)}>
            {item.path}
          </button>
        ))}
      </div>

      <div className="mt-4 overflow-hidden rounded-lg border border-warm-150">
        <div className="flex items-center justify-between border-b border-warm-150 bg-warm-50 px-3 py-2 text-sm">
          <span className="font-mono text-warm-700">{file.path}</span>
          <button className="btn-secondary text-xs px-3 py-1" onClick={copyContent}>{copied ? '已复制' : '复制代码'}</button>
        </div>
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap bg-warm-900 p-4 text-sm leading-6 text-warm-100"><code>{file.content}</code></pre>
      </div>

      <div className="mt-4">
        <div className="mb-2 text-h4 text-warm-700">Git Diff</div>
        <DiffBubble value={generated.diff || '暂无 diff'} />
      </div>
    </div>
  );
}