'use client';

import { useState, useEffect, useCallback } from 'react';
import type { AgentIdentity } from '../../types';

const STATUS_OPTIONS = ['pending', 'active', 'suspended', 'revoked'] as const;
const STATUS_COLORS: Record<string, string> = {
  pending: 'bg-amber-50 text-amber-600 border-amber-200',
  active: 'bg-green-50 text-green-600 border-green-200',
  suspended: 'bg-orange-50 text-orange-600 border-orange-200',
  revoked: 'bg-red-50 text-red-600 border-red-200',
};

export default function AgentIdentityCard({
  authHeaders,
  setNotice,
}: {
  authHeaders: () => Record<string, string>;
  setNotice: (msg: string) => void;
}): JSX.Element {
  const [identities, setIdentities] = useState<AgentIdentity[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ agent_id: '', tenant_id: 'default' });
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const headers = authHeaders();

  const fetchIdentities = useCallback(async () => {
    try {
      const res = await fetch('/digital/identity', { headers });
      if (res.ok) setIdentities(await res.json());
    } catch { /* ignore */ }
  }, [headers]);

  useEffect(() => {
    fetchIdentities().then(() => setLoading(false));
  }, [fetchIdentities]);

  const handleCreate = async () => {
    if (!form.agent_id.trim()) { setNotice('请输入 Agent ID'); return; }
    try {
      const res = await fetch('/digital/identity', {
        method: 'POST',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      });
      if (res.ok) {
        setNotice('数字身份创建成功');
        setShowCreate(false);
        setForm({ agent_id: '', tenant_id: 'default' });
        fetchIdentities();
      }
    } catch { setNotice('创建失败'); }
  };

  const handleAction = async (agentId: string, action: 'email' | 'ssh' | 'oauth2') => {
    try {
      let body = '{}';
      if (action === 'oauth2') {
        const provider = prompt('OAuth2 Provider (e.g. google, github):') || 'github';
        const creds = prompt('OAuth2 Credentials:') || `token-${Date.now()}`;
        body = JSON.stringify({ provider, creds });
      }
      const res = await fetch(`/digital/identity/${encodeURIComponent(agentId)}/${action}`, {
        method: 'POST',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body,
      });
      if (res.ok) {
        setNotice(`${action} ${action === 'email' ? '邮箱' : action === 'ssh' ? 'SSH密钥' : 'OAuth2'} 已生成`);
        fetchIdentities();
      }
    } catch { setNotice('操作失败'); }
  };

  const handleUpdateStatus = async (agentId: string, status: string) => {
    try {
      await fetch(`/digital/identity/${encodeURIComponent(agentId)}`, {
        method: 'PUT',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      });
      setNotice(`状态已更新为 ${status}`);
      fetchIdentities();
    } catch { setNotice('更新失败'); }
  };

  const handleDelete = async (agentId: string) => {
    if (!confirm(`确认删除 ${agentId} 的数字身份?`)) return;
    try {
      await fetch(`/digital/identity/${encodeURIComponent(agentId)}`, { method: 'DELETE', headers });
      setNotice('已删除');
      fetchIdentities();
    } catch { setNotice('删除失败'); }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-h4">[id] Agent 数字身份</h3>
          <p className="text-xs text-gray-400 mt-1">管理 Agent 的邮箱、SSH 密钥和 OAuth2 凭证</p>
        </div>
        <button onClick={() => setShowCreate(true)} className="btn-primary text-sm">
          + 创建身份
        </button>
      </div>

      {/* Create form */}
      {showCreate && (
        <div className="card p-5 border-2 border-primary-100 bg-primary-50/30">
          <h4 className="font-semibold text-gray-800 mb-3">创建 Agent 数字身份</h4>
          <div className="grid grid-cols-2 gap-4 mb-4">
            <div>
              <label className="block text-xs text-gray-500 mb-1">Agent ID *</label>
              <input
                type="text"
                value={form.agent_id}
                onChange={(e) => setForm((f) => ({ ...f, agent_id: e.target.value }))}
                placeholder="例如: coder-agent-01"
                className="input-field text-sm w-full"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Tenant ID</label>
              <input
                type="text"
                value={form.tenant_id}
                onChange={(e) => setForm((f) => ({ ...f, tenant_id: e.target.value }))}
                className="input-field text-sm w-full"
              />
            </div>
          </div>
          <div className="flex gap-2">
            <button onClick={handleCreate} className="btn-primary text-sm">创建</button>
            <button onClick={() => setShowCreate(false)} className="btn-secondary text-sm">取消</button>
          </div>
        </div>
      )}

      {/* Identity cards */}
      <div className="grid gap-4">
        {identities.map((ident) => (
          <div key={ident.agent_id} className="card overflow-hidden">
            {/* Header row */}
            <div
              className="flex items-center justify-between p-4 cursor-pointer hover:bg-gray-50 transition-colors"
              onClick={() => setExpandedId(expandedId === ident.agent_id ? null : ident.agent_id)}
            >
              <div className="flex items-center gap-3">
                <span className="text-2xl">[id]</span>
                <div>
                  <h4 className="font-semibold text-gray-800 font-mono text-sm">{ident.agent_id}</h4>
                  <p className="text-xs text-gray-400">{ident.email || '(未分配邮箱)'}</p>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <span className={`text-xs px-2 py-1 rounded-full border font-medium ${STATUS_COLORS[ident.status] || 'bg-gray-50 text-gray-500'}`}>
                  {ident.status}
                </span>
                <span className="text-gray-300 text-xs">{expandedId === ident.agent_id ? '[up]' : '[down]'}</span>
              </div>
            </div>

            {/* Expanded detail */}
            {expandedId === ident.agent_id && (
              <div className="border-t border-gray-100 p-4 bg-gray-50/50 space-y-4">
                {/* Quick actions */}
                <div className="flex flex-wrap gap-2">
                  <button onClick={() => handleAction(ident.agent_id, 'email')} className="btn-ghost text-xs">
                    [mail] 生成邮箱
                  </button>
                  <button onClick={() => handleAction(ident.agent_id, 'ssh')} className="btn-ghost text-xs">
                    [key] 生成 SSH
                  </button>
                  <button onClick={() => handleAction(ident.agent_id, 'oauth2')} className="btn-ghost text-xs">
                    [lock] 设置 OAuth2
                  </button>
                  <select
                    value={ident.status}
                    onChange={(e) => handleUpdateStatus(ident.agent_id, e.target.value)}
                    className="text-xs border rounded px-2 py-1"
                  >
                    {STATUS_OPTIONS.map((s) => (
                      <option key={s} value={s}>{s}</option>
                    ))}
                  </select>
                  <button
                    onClick={() => handleDelete(ident.agent_id)}
                    className="btn-ghost text-xs text-red-500 hover:text-red-700"
                  >
                    [delete] 删除
                  </button>
                </div>

                {/* Detail grid */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
                  <div className="space-y-1">
                    <span className="text-gray-400">身份 ID</span>
                    <p className="font-mono text-gray-600">{ident.id}</p>
                  </div>
                  <div className="space-y-1">
                    <span className="text-gray-400">邮箱</span>
                    <p className="font-mono text-blue-600">{ident.email || '-'}</p>
                  </div>
                  <div className="space-y-1">
                    <span className="text-gray-400">SSH 密钥 ({ident.ssh_key_type})</span>
                    <p className="font-mono text-gray-600 break-all text-[11px]">{ident.ssh_pubkey || '(未生成)'}</p>
                  </div>
                  <div className="space-y-1">
                    <span className="text-gray-400">OAuth2 Provider</span>
                    <p className="font-mono text-gray-600">{ident.oauth2_provider || '(未配置)'}</p>
                  </div>
                  <div className="space-y-1">
                    <span className="text-gray-400">GP G Key</span>
                    <p className="font-mono text-gray-600">{ident.gpg_key || '(未配置)'}</p>
                  </div>
                  <div className="space-y-1">
                    <span className="text-gray-400">创建时间</span>
                    <p className="text-gray-500">{new Date(ident.created_at).toLocaleString()}</p>
                  </div>
                </div>
              </div>
            )}
          </div>
        ))}

        {identities.length === 0 && !showCreate && (
          <div className="text-center py-16 text-gray-400">
            <p className="text-5xl mb-4">[id]</p>
            <p className="text-lg mb-1">暂无 Agent 数字身份</p>
            <p className="text-sm">创建身份后，Agent 将拥有邮箱、SSH 和 OAuth2 凭证</p>
          </div>
        )}
      </div>
    </div>
  );
}
