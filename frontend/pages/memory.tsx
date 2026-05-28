import { useEffect, useState, type JSX } from 'react';
import MemoryPanel from '../components/memory/MemoryPanel';
import type { MemoryFileInfo, User } from '../types';

const MEMORY_TABS = ['全部', 'user', 'feedback', 'project', 'reference'] as const;

export default function MemoryPage(): JSX.Element {
  const [user, setUser] = useState<User | null>(null);
  const [memories, setMemories] = useState<MemoryFileInfo[]>([]);
  const [filter, setFilter] = useState<string>('全部');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [activeFile, setActiveFile] = useState<string | null>(null);
  const [notice, setNotice] = useState('');

  useEffect(() => {
    const u = localStorage.getItem('agenthub_user');
    if (u) setUser(JSON.parse(u) as User);
    void loadMemories();
  }, []);

  async function loadMemories() {
    setLoading(true);
    setError('');
    try {
      const params = filter !== '全部' ? `?type=${filter}` : '';
      const res = await fetch(`/api/memory/files${params}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setMemories((await res.json()) as MemoryFileInfo[]);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }

  async function handleDelete(filename: string) {
    if (!confirm(`确定删除 "${filename}"？`)) return;
    try {
      const res = await fetch(`/api/memory/files/${encodeURIComponent(filename)}`, {
        method: 'DELETE',
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setNotice(`已删除: ${filename}`);
      setActiveFile(null);
      await loadMemories();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '删除失败');
    }
  }

  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState({
    name: '',
    description: '',
    type: 'reference' as string,
    body: '',
    filename: '',
  });

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!createForm.name.trim()) return;
    try {
      const body: Record<string, unknown> = {
        name: createForm.name.trim(),
        description: createForm.description.trim(),
        type: createForm.type,
        body: createForm.body,
      };
      if (createForm.filename.trim()) body.filename = createForm.filename.trim();
      const res = await fetch('/api/memory/files', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setNotice(`已创建: ${createForm.name}`);
      setShowCreate(false);
      setCreateForm({ name: '', description: '', type: 'reference', body: '', filename: '' });
      await loadMemories();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '创建失败');
    }
  }

  return (
    <div style={{ display: 'flex', height: '100vh', fontFamily: 'system-ui, sans-serif' }}>
      {/* Sidebar */}
      <aside style={{
        width: 280, borderRight: '1px solid #e5e7eb', display: 'flex',
        flexDirection: 'column', background: '#f9fafb',
      }}>
        <div style={{ padding: '16px', borderBottom: '1px solid #e5e7eb' }}>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>记忆系统</h2>
          <p style={{ margin: '4px 0 0', fontSize: 12, color: '#6b7280' }}>
            跨会话持久化上下文
          </p>
        </div>

        {/* tabs */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, padding: '8px 12px', borderBottom: '1px solid #e5e7eb' }}>
          {MEMORY_TABS.map(t => (
            <button
              key={t}
              onClick={() => { setFilter(t); }}
              style={{
                padding: '4px 10px', fontSize: 12, borderRadius: 12, border: '1px solid #d1d5db',
                cursor: 'pointer', background: filter === t ? '#3b82f6' : '#fff',
                color: filter === t ? '#fff' : '#374151',
              }}
            >
              {t === '全部' ? '全部' : t}
            </button>
          ))}
        </div>

        {/* file list */}
        <div style={{ flex: 1, overflow: 'auto', padding: 8 }}>
          {loading && <p style={{ fontSize: 13, color: '#6b7280', padding: 8 }}>加载中...</p>}
          {error && <p style={{ fontSize: 13, color: '#ef4444', padding: 8 }}>{error}</p>}
          {!loading && !error && memories.length === 0 && (
            <p style={{ fontSize: 13, color: '#9ca3af', padding: 8 }}>暂无记忆文件</p>
          )}
          {memories.map(m => (
            <div
              key={m.filename}
              onClick={() => setActiveFile(m.filename)}
              style={{
                padding: '8px 12px', borderRadius: 6, cursor: 'pointer', marginBottom: 4,
                background: activeFile === m.filename ? '#eff6ff' : 'transparent',
                border: activeFile === m.filename ? '1px solid #bfdbfe' : '1px solid transparent',
              }}
            >
              <div style={{ fontWeight: 500, fontSize: 14, color: '#111827' }}>{m.name}</div>
              <div style={{ fontSize: 11, color: '#6b7280', marginTop: 2 }}>
                <span style={{
                  display: 'inline-block', padding: '1px 6px', borderRadius: 8,
                  background: '#e5e7eb', marginRight: 6, fontSize: 10,
                }}>{m.type}</span>
                {m.description.slice(0, 40)}
              </div>
              <div style={{ fontSize: 10, color: '#9ca3af', marginTop: 2 }}>
                {m.updated_at || new Date(m.mtime * 1000).toLocaleString()}
              </div>
            </div>
          ))}
        </div>

        {/* extraction controls + create button */}
        <div style={{ padding: '8px 12px', borderTop: '1px solid #e5e7eb' }}>
          <div style={{ display: 'flex', gap: 4, marginBottom: 6 }}>
            <button
              onClick={async () => {
                try {
                  setNotice('正在提取记忆...');
                  const res = await fetch('/api/memory/extraction/backfill');
                  const data = await res.json() as { total_memories_saved?: number; sessions_processed?: number };
                  setNotice(`提取完成: ${data.total_memories_saved || 0} 条新记忆 (处理 ${data.sessions_processed || 0} 个会话)`);
                  await loadMemories();
                } catch { setNotice('提取失败'); }
              }}
              style={{
                flex: 1, padding: '6px 0', fontSize: 11, fontWeight: 500,
                background: '#f3f4f6', color: '#374151', border: '1px solid #d1d5db', borderRadius: 4,
                cursor: 'pointer',
              }}
            >
              提取全部会话
            </button>
            <button
              onClick={async () => {
                try {
                  const res = await fetch('/api/memory/extraction/reset');
                  if (res.ok) setNotice('已重置所有提取游标');
                } catch { setNotice('重置失败'); }
              }}
              style={{
                padding: '6px 8px', fontSize: 11, background: '#fef2f2', color: '#dc2626',
                border: '1px solid #fca5a5', borderRadius: 4, cursor: 'pointer',
              }}
              title="重置提取游标"
            >
              重置
            </button>
          </div>
          <button
            onClick={() => setShowCreate(true)}
            style={{
              width: '100%', padding: '8px 0', fontSize: 13, fontWeight: 500,
              background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 6,
              cursor: 'pointer',
            }}
          >
            + 新建记忆
          </button>
        </div>
      </aside>

      {/* main content */}
      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', background: '#fff' }}>
        {notice && (
          <div style={{
            padding: '8px 16px', background: '#dbeafe', color: '#1e40af',
            fontSize: 13, display: 'flex', justifyContent: 'space-between',
          }}>
            <span>{notice}</span>
            <button onClick={() => setNotice('')} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#1e40af' }}>✕</button>
          </div>
        )}

        {activeFile ? (
          <MemoryPanel
            filename={activeFile}
            key={activeFile}
            onDelete={handleDelete}
            onError={setError}
            onRefresh={loadMemories}
          />
        ) : showCreate ? (
          /* create form */
          <form onSubmit={handleCreate} style={{ padding: 24, maxWidth: 600 }}>
            <h3 style={{ margin: '0 0 16px', fontSize: 16, fontWeight: 600 }}>新建记忆</h3>
            <div style={{ marginBottom: 12 }}>
              <label style={{ display: 'block', fontSize: 13, fontWeight: 500, marginBottom: 4 }}>名称 *</label>
              <input value={createForm.name} onChange={e => setCreateForm(f => ({ ...f, name: e.target.value }))}
                style={{ width: '100%', padding: '8px 12px', borderRadius: 6, border: '1px solid #d1d5db', fontSize: 14 }}
                required />
            </div>
            <div style={{ marginBottom: 12 }}>
              <label style={{ display: 'block', fontSize: 13, fontWeight: 500, marginBottom: 4 }}>描述</label>
              <input value={createForm.description} onChange={e => setCreateForm(f => ({ ...f, description: e.target.value }))}
                style={{ width: '100%', padding: '8px 12px', borderRadius: 6, border: '1px solid #d1d5db', fontSize: 14 }} />
            </div>
            <div style={{ marginBottom: 12 }}>
              <label style={{ display: 'block', fontSize: 13, fontWeight: 500, marginBottom: 4 }}>类型</label>
              <select value={createForm.type} onChange={e => setCreateForm(f => ({ ...f, type: e.target.value }))}
                style={{ width: '100%', padding: '8px 12px', borderRadius: 6, border: '1px solid #d1d5db', fontSize: 14 }}>
                <option value="user">user - 用户角色与偏好</option>
                <option value="feedback">feedback - 工作方式反馈</option>
                <option value="project">project - 项目上下文</option>
                <option value="reference">reference - 外部系统指针</option>
              </select>
            </div>
            <div style={{ marginBottom: 12 }}>
              <label style={{ display: 'block', fontSize: 13, fontWeight: 500, marginBottom: 4 }}>文件名（可选，自动生成）</label>
              <input value={createForm.filename} onChange={e => setCreateForm(f => ({ ...f, filename: e.target.value }))}
                style={{ width: '100%', padding: '8px 12px', borderRadius: 6, border: '1px solid #d1d5db', fontSize: 14 }}
                placeholder="例如: user_preferences.md" />
            </div>
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontSize: 13, fontWeight: 500, marginBottom: 4 }}>内容 (Markdown)</label>
              <textarea value={createForm.body} onChange={e => setCreateForm(f => ({ ...f, body: e.target.value }))}
                rows={8}
                style={{ width: '100%', padding: '8px 12px', borderRadius: 6, border: '1px solid #d1d5db', fontSize: 14, fontFamily: 'monospace' }} />
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button type="submit" style={{
                padding: '8px 20px', background: '#3b82f6', color: '#fff', border: 'none',
                borderRadius: 6, fontSize: 14, cursor: 'pointer',
              }}>创建</button>
              <button type="button" onClick={() => setShowCreate(false)} style={{
                padding: '8px 20px', background: '#f3f4f6', color: '#374151', border: '1px solid #d1d5db',
                borderRadius: 6, fontSize: 14, cursor: 'pointer',
              }}>取消</button>
            </div>
          </form>
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#9ca3af' }}>
            <div style={{ textAlign: 'center' }}>
              <p style={{ fontSize: 24, margin: '0 0 8px' }}>📝</p>
              <p style={{ fontSize: 14 }}>选择一个记忆文件查看，或点击"新建记忆"</p>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
