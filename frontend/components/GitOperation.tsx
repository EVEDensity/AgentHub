import { useState, type JSX } from 'react';

interface GitOperationProps {
  sessionId: string;
}

interface GitResponse {
  branch?: string;
  commit_hash?: string;
  detail?: string;
}

export default function GitOperation({ sessionId }: GitOperationProps): JSX.Element {
  const [branchName, setBranchName] = useState<string>('feature/agenthub-demo');
  const [loading, setLoading] = useState<boolean>(false);
  const [notice, setNotice] = useState<string>('');

  async function post(url: string, body: Record<string, unknown>): Promise<void> {
    setLoading(true);
    setNotice('');
    try {
      const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const data: GitResponse = await res.json();
      setNotice(res.ok ? (data.branch ? `分支 ${data.branch} 创建成功` : `提交成功：${data.commit_hash?.slice(0, 8)}`) : data.detail || '操作失败');
    } catch {
      setNotice('网络错误，请确认后端已启动');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-3 text-lg font-semibold">Git 操作</div>
      <div className="flex flex-wrap gap-2">
        <input value={branchName} onChange={(e) => setBranchName(e.target.value)}
          className="w-60 rounded-lg border px-3 py-2 outline-none focus:border-blue-500" />
        <button disabled={loading}
          className="rounded-xl border bg-white px-4 py-2 text-slate-600 hover:bg-slate-50 disabled:opacity-50"
          onClick={() => post('/api/git/branch', { branchName, sessionId })}>创建分支</button>
        <button disabled={loading}
          className="rounded-xl bg-blue-600 px-4 py-2 text-white hover:bg-blue-700 disabled:opacity-50"
          onClick={() => post('/api/git/commit', { sessionId, message: 'Agent 自动提交' })}>提交代码</button>
      </div>
      {notice && <div className="mt-2 text-sm text-slate-500">{notice}</div>}
    </div>
  );
}