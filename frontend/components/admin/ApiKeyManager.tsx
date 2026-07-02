'use client';

import { useState, useEffect, type JSX, type FormEvent } from 'react';
import { useToolStore, type ApiKeyInfo } from '../../stores/toolStore';

export default function ApiKeyManager(): JSX.Element {
  const { apiKeys, apiKeysLoading, loadApiKeys, createApiKey, revokeApiKey } = useToolStore();
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newScopes, setNewScopes] = useState('chat');
  const [newRateLimit, setNewRateLimit] = useState(60);
  const [createdKey, setCreatedKey] = useState<ApiKeyInfo | null>(null);

  useEffect(() => {
    loadApiKeys();
  }, []);

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault();
    const key = await createApiKey(newName, newScopes.split(',').map((s) => s.trim()), newRateLimit);
    if (key) {
      setCreatedKey(key);
      setNewName('');
      setNewScopes('chat');
      setNewRateLimit(60);
    }
  };

  const handleRevoke = async (id: string) => {
    if (!confirm('确定要撤销此 API Key？撤销后将立即失效。')) return;
    await revokeApiKey(id);
  };

  return (
    <section className="space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-[34px] font-semibold leading-tight text-warm-900">API 密钥管理</h2>
          <p className="mt-1 text-sm text-warm-500">
            创建 API Key 以通过 <code className="text-xs bg-warm-100 px-1 rounded">/v1/public/chat</code> 端点调用 Agent。
          </p>
        </div>
        <button className="btn-primary" onClick={() => { setShowCreate(true); setCreatedKey(null); }}>
          + 创建 API Key
        </button>
      </div>

      {/* Create modal */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 px-4 pt-[10vh] pb-8 overflow-y-auto" onClick={() => setShowCreate(false)}>
          <div className="w-full max-w-lg rounded-2xl bg-white shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-warm-150 px-6 py-4">
              <h3 className="text-lg font-semibold text-warm-900">🔑 创建 API Key</h3>
              <button className="rounded-lg px-3 py-1.5 text-sm text-warm-500 hover:bg-warm-100" onClick={() => setShowCreate(false)}>关闭</button>
            </div>

            {createdKey ? (
              /* Success state — show the key once */
              <div className="px-6 py-5 space-y-4">
                <div className="rounded-xl bg-green-50 border border-green-200 p-4 text-center">
                  <p className="text-sm font-medium text-green-800">✅ API Key 创建成功</p>
                  <p className="text-xs text-green-600 mt-1">请立即复制密钥，关闭后将无法再次查看。</p>
                  <div className="mt-3 bg-white rounded-lg border border-green-200 px-4 py-3 font-mono text-sm text-green-900 break-all select-all">
                    {createdKey.fullKey}
                  </div>
                  <div className="mt-3 flex gap-2 justify-center">
                    <button
                      className="btn-secondary text-xs"
                      onClick={() => {
                        navigator.clipboard.writeText(createdKey.fullKey || '');
                      }}
                    >
                      📋 复制
                    </button>
                    <button className="btn-primary text-xs" onClick={() => { setShowCreate(false); setCreatedKey(null); loadApiKeys(); }}>
                      我已保存，关闭
                    </button>
                  </div>
                </div>

                <div className="text-xs text-warm-500 space-y-1">
                  <p><strong>名称：</strong>{createdKey.name}</p>
                  <p><strong>权限范围：</strong>{createdKey.scopes.join(', ')}</p>
                  <p><strong>速率限制：</strong>{createdKey.rateLimit} 次/分钟</p>
                </div>
              </div>
            ) : (
              /* Create form */
              <form className="px-6 py-5 space-y-4" onSubmit={handleCreate}>
                <div>
                  <label className="block text-sm font-medium text-warm-600 mb-1">名称</label>
                  <input
                    className="input-field w-full"
                    placeholder="例如：生产环境 API、移动端 App"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    required
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-warm-600 mb-1">
                    权限范围（逗号分隔）
                  </label>
                  <input
                    className="input-field w-full"
                    placeholder="chat, knowledge, agent"
                    value={newScopes}
                    onChange={(e) => setNewScopes(e.target.value)}
                  />
                  <p className="text-[10px] text-warm-400 mt-1">
                    可选：chat（对话）、knowledge（知识库）、agent（Agent管理）
                  </p>
                </div>
                <div>
                  <label className="block text-sm font-medium text-warm-600 mb-1">
                    速率限制（次/分钟）
                  </label>
                  <input
                    className="input-field w-32"
                    type="number"
                    min={1}
                    max={1000}
                    value={newRateLimit}
                    onChange={(e) => setNewRateLimit(parseInt(e.target.value) || 60)}
                  />
                </div>
                <div className="rounded-lg bg-blue-50 border border-blue-200 px-3 py-2 text-xs text-blue-700">
                  <strong>使用方式：</strong>
                  <code className="ml-2 bg-blue-100 px-1 rounded text-[10px]">
                    curl -H &quot;Authorization: Bearer YOUR_KEY&quot; /v1/public/chat -d &apos;{'{'}...{'}'}&apos;
                  </code>
                </div>
                <div className="flex justify-end gap-2 pt-2 border-t border-warm-150">
                  <button type="button" className="btn-secondary" onClick={() => setShowCreate(false)}>取消</button>
                  <button type="submit" className="btn-primary">创建</button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}

      {/* API Keys list */}
      {apiKeysLoading ? (
        <div className="text-center py-12 text-warm-400">加载中...</div>
      ) : (
        <div className="space-y-2">
          {apiKeys.length === 0 ? (
            <div className="card text-center py-12">
              <p className="text-sm text-warm-400">暂无 API Key</p>
              <p className="text-xs text-warm-400 mt-1">创建一个 API Key 以开始使用公开 API</p>
            </div>
          ) : (
            apiKeys.map((key) => (
              <div key={key.id} className={`card flex items-center justify-between ${key.enabled ? '' : 'opacity-50'}`}>
                <div className="flex items-center gap-3">
                  <span className={`material-symbols-outlined text-[20px] ${key.enabled ? 'text-primary-500' : 'text-warm-300'}`}>key</span>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-warm-800">{key.name}</span>
                      <code className="text-[10px] bg-warm-100 px-1.5 py-0.5 rounded text-warm-500">{key.keyPrefix}...</code>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded ${key.enabled ? 'bg-green-50 text-green-600' : 'bg-warm-100 text-warm-400'}`}>
                        {key.enabled ? '启用' : '已撤销'}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 mt-0.5 text-[10px] text-warm-400">
                      <span>权限: {key.scopes.join(', ')}</span>
                      <span>·</span>
                      <span>{key.rateLimit} 次/分钟</span>
                      <span>·</span>
                      <span>创建: {new Date(key.createdAt).toLocaleDateString('zh-CN')}</span>
                      {key.lastUsedAt && (
                        <>
                          <span>·</span>
                          <span>最后使用: {new Date(key.lastUsedAt).toLocaleDateString('zh-CN')}</span>
                        </>
                      )}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    className="btn-ghost text-xs px-2 py-1 text-red-500"
                    onClick={() => handleRevoke(key.id)}
                    disabled={!key.enabled}
                  >
                    撤销
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {/* API documentation */}
      <div className="card">
        <h3 className="text-sm font-semibold text-warm-700 mb-3">📖 公开 API 文档</h3>
        <div className="space-y-3 text-sm">
          <div>
            <p className="font-medium text-warm-700">POST /v1/public/chat</p>
            <p className="text-xs text-warm-500 mt-0.5">发送消息给指定 Agent，支持流式响应 (SSE)</p>
            <pre className="mt-2 bg-warm-900 text-green-400 text-xs p-4 rounded-lg overflow-x-auto">
{`curl -X POST https://your-domain/v1/public/chat \\
  -H "Authorization: Bearer ah-xxxxxxxxxxxx" \\
  -H "Content-Type: application/json" \\
  -d '{
    "message": "你好，帮我分析一下这个数据",
    "agent_id": "data-analyst",
    "stream": true,
    "session_id": "optional-session-id"
  }'`}
            </pre>
          </div>
          <div className="grid grid-cols-2 gap-3 text-xs text-warm-600">
            <div className="rounded-lg border border-warm-150 p-3">
              <p className="font-medium text-warm-700">请求参数</p>
              <ul className="mt-1 space-y-1 text-warm-500">
                <li><code>message</code> (必填) — 消息内容</li>
                <li><code>agent_id</code> (可选) — 指定 Agent</li>
                <li><code>stream</code> (可选) — 是否流式响应</li>
                <li><code>session_id</code> (可选) — 会话 ID</li>
              </ul>
            </div>
            <div className="rounded-lg border border-warm-150 p-3">
              <p className="font-medium text-warm-700">响应格式</p>
              <ul className="mt-1 space-y-1 text-warm-500">
                <li><code>status</code> — accepted / error</li>
                <li><code>session_id</code> — 会话 ID</li>
                <li><code>trace_id</code> — 追踪 ID</li>
                <li><code>stream</code> — 是否流式</li>
              </ul>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
