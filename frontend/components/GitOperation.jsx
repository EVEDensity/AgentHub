import { useState } from 'react';

export default function GitOperation({ sessionId }) {
  const [branchName, setBranchName] = useState('feature/agenthub-demo');
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState('');

  async function post(url, body) {
    setLoading(true);
    setNotice('');
    try {
      const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const data = await res.json();
      setNotice(res.ok ? (data.branch ? `分支 ${data.branch} 创建成功` : `提交成功：${data.commit_hash?.slice(0, 8)}`) : data.detail || '操作失败');
    } catch {
      setNotice('网络错误，请确认后端已启动');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="card p-4">
      <div className="mb-3 text-h4">Git 操作</div>
      <div className="flex flex-wrap gap-2">
        <input value={branchName} onChange={(e) => setBranchName(e.target.value)} className="input-field w-60" />
        <button disabled={loading} className="btn-secondary disabled:opacity-50" onClick={() => post('/api/git/branch', { branchName, sessionId })}>创建分支</button>
        <button disabled={loading} className="btn-primary disabled:opacity-50" onClick={() => post('/api/git/commit', { sessionId, message: 'Agent 自动提交' })}>提交代码</button>
      </div>
      {notice && <div className="mt-2 text-caption">{notice}</div>}
    </div>
  );
}