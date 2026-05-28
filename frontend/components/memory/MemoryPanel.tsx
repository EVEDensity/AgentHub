import { useEffect, useState, type JSX } from 'react';
import type { MemoryDetail, MemoryFreshnessInfo } from '../../types';

interface Props {
  filename: string;
  onDelete: (filename: string) => void;
  onError: (err: string) => void;
  onRefresh: () => void;
}

export default function MemoryPanel({ filename, onDelete, onError, onRefresh }: Props): JSX.Element {
  const [detail, setDetail] = useState<MemoryDetail | null>(null);
  const [freshness, setFreshness] = useState<MemoryFreshnessInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState({ name: '', description: '', type: '', body: '' });

  useEffect(() => {
    setLoading(true);
    setEditing(false);
    void Promise.all([loadDetail(), loadFreshness()]);
  }, [filename]);

  async function loadDetail() {
    try {
      const res = await fetch(`/api/memory/files/${encodeURIComponent(filename)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = (await res.json()) as MemoryDetail;
      setDetail(d);
      setEditForm({
        name: d.meta.name,
        description: d.meta.description,
        type: d.meta.type,
        body: d.body,
      });
    } catch (e: unknown) {
      onError(e instanceof Error ? e.message : '加载详情失败');
    } finally {
      setLoading(false);
    }
  }

  async function loadFreshness() {
    try {
      const res = await fetch('/api/memory/freshness');
      if (!res.ok) return;
      const data = (await res.json()) as { freshness: MemoryFreshnessInfo[] };
      const f = data.freshness.find(f => f.filename === filename);
      if (f) setFreshness(f);
    } catch {
      // non-critical
    }
  }

  async function handleSave() {
    if (!detail) return;
    try {
      const res = await fetch(`/api/memory/files/${encodeURIComponent(filename)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: editForm.name,
          description: editForm.description,
          type: editForm.type,
          body: editForm.body,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setEditing(false);
      onRefresh();
      await loadDetail();
    } catch (e: unknown) {
      onError(e instanceof Error ? e.message : '保存失败');
    }
  }

  if (loading) {
    return <div style={{ padding: 24, color: '#6b7280', fontSize: 14 }}>加载中...</div>;
  }

  if (!detail) {
    return <div style={{ padding: 24, color: '#ef4444', fontSize: 14 }}>无法加载记忆文件</div>;
  }

  const typeColors: Record<string, string> = {
    user: '#3b82f6',
    feedback: '#f59e0b',
    project: '#10b981',
    reference: '#8b5cf6',
  };

  return (
    <div style={{ padding: 24, maxWidth: 800 }}>
      {/* header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
        <div>
          <h3 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>
            {editing ? (
              <input value={editForm.name} onChange={e => setEditForm(f => ({ ...f, name: e.target.value }))}
                style={{ fontSize: 18, padding: '4px 8px', borderRadius: 4, border: '1px solid #d1d5db', width: 300 }} />
            ) : detail.meta.name}
          </h3>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 8 }}>
            <span style={{
              display: 'inline-block', padding: '2px 8px', borderRadius: 10, fontSize: 11,
              background: typeColors[detail.meta.type] || '#6b7280', color: '#fff',
            }}>{detail.meta.type}</span>
            <span style={{ fontSize: 11, color: '#6b7280' }}>
              创建: {detail.meta.created_at || '未知'}
            </span>
            <span style={{ fontSize: 11, color: '#6b7280' }}>
              更新: {detail.meta.updated_at || '未知'}
            </span>
            {freshness?.warning && (
              <span style={{ fontSize: 11, color: '#f59e0b' }}>{freshness.warning}</span>
            )}
          </div>
        </div>

        <div style={{ display: 'flex', gap: 6 }}>
          <button onClick={() => setEditing(!editing)} style={{
            padding: '6px 14px', fontSize: 12, borderRadius: 6, border: '1px solid #d1d5db',
            cursor: 'pointer', background: editing ? '#10b981' : '#f3f4f6',
            color: editing ? '#fff' : '#374151',
          }}>
            {editing ? '预览' : '编辑'}
          </button>
          <button onClick={() => onDelete(filename)} style={{
            padding: '6px 14px', fontSize: 12, borderRadius: 6, border: '1px solid #fca5a5',
            cursor: 'pointer', background: '#fef2f2', color: '#dc2626',
          }}>
            删除
          </button>
        </div>
      </div>

      {/* description */}
      {editing ? (
        <div style={{ marginBottom: 16 }}>
          <label style={{ display: 'block', fontSize: 12, fontWeight: 500, color: '#6b7280', marginBottom: 4 }}>描述</label>
          <input value={editForm.description} onChange={e => setEditForm(f => ({ ...f, description: e.target.value }))}
            style={{ width: '100%', padding: '6px 10px', borderRadius: 4, border: '1px solid #d1d5db', fontSize: 13 }} />
        </div>
      ) : detail.meta.description && (
        <p style={{ fontSize: 13, color: '#6b7280', margin: '0 0 16px', padding: '8px 12px', background: '#f9fafb', borderRadius: 6 }}>
          {detail.meta.description}
        </p>
      )}

      {/* body */}
      {editing ? (
        <div>
          <label style={{ display: 'block', fontSize: 12, fontWeight: 500, color: '#6b7280', marginBottom: 4 }}>内容 (Markdown)</label>
          <textarea value={editForm.body} onChange={e => setEditForm(f => ({ ...f, body: e.target.value }))}
            rows={20}
            style={{ width: '100%', padding: '12px', borderRadius: 6, border: '1px solid #d1d5db', fontSize: 13, fontFamily: 'monospace', lineHeight: 1.5 }}
          />
          <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
            <button onClick={handleSave} style={{
              padding: '8px 20px', background: '#3b82f6', color: '#fff', border: 'none',
              borderRadius: 6, fontSize: 13, cursor: 'pointer',
            }}>保存</button>
            <button onClick={() => setEditing(false)} style={{
              padding: '8px 20px', background: '#f3f4f6', color: '#374151', border: '1px solid #d1d5db',
              borderRadius: 6, fontSize: 13, cursor: 'pointer',
            }}>取消</button>
          </div>
        </div>
      ) : (
        <div style={{
          padding: 16, border: '1px solid #e5e7eb', borderRadius: 8,
          background: '#fafafa', minHeight: 200, whiteSpace: 'pre-wrap',
          fontFamily: 'monospace', fontSize: 13, lineHeight: 1.6,
        }}>
          {detail.body || '(空内容)'}
        </div>
      )}

      {/* file info footer */}
      <div style={{ marginTop: 16, padding: '8px 12px', background: '#f9fafb', borderRadius: 6, fontSize: 11, color: '#9ca3af' }}>
        文件: {filename} | 路径: {detail.filename}
      </div>
    </div>
  );
}
