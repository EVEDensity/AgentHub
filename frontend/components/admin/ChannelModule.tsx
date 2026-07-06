'use client';

import { useState, type JSX, type FormEvent } from 'react';

// ── Types ─────────────────────────────────────────────────────────

interface ChannelConfig {
  platform: string;
  webhook_url: string;
  verify_token: string;
  signing_secret: string;
  enabled: boolean;
  agent_id: string;
  bot_name: string;
}

const PLATFORM_INFO: Record<string, { name: string; icon: string; color: string; guide: string }> = {
  feishu: {
    name: '飞书 (Lark)',
    icon: '[bird]',
    color: '#3370FF',
    guide: '在飞书开发者后台创建应用 → 启用机器人能力 → 配置事件订阅 URL → 获取 App ID / App Secret',
  },
  wecom: {
    name: '企业微信',
    icon: '[heart]',
    color: '#07C160',
    guide: '在企业微信管理后台创建应用 → 设置接收消息 URL → 配置 Token 和 EncodingAESKey',
  },
};

// ── Main Component ────────────────────────────────────────────────

export default function ChannelModule({ authHeaders, setNotice }: {
  authHeaders: () => Record<string, string>;
  setNotice: (msg: string) => void;
}): JSX.Element {
  const [configs, setConfigs] = useState<ChannelConfig[]>([]);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<ChannelConfig | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [newPlatform, setNewPlatform] = useState('feishu');

  // Load configs
  const loadConfigs = async () => {
    setLoading(true);
    try {
      const res = await fetch('/platform/channels', { headers: authHeaders() });
      const data = await res.json();
      setConfigs(data.channels || []);
    } catch { /* ignore */ }
    setLoading(false);
  };

  // Called on mount
  useState(() => { loadConfigs(); });

  // Save config
  const handleSave = async (cfg: ChannelConfig) => {
    try {
      const res = await fetch('/platform/channels', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(cfg),
      });
      if (res.ok) {
        setNotice(`${cfg.platform} 渠道配置已保存`);
        setEditing(null);
        setShowAdd(false);
        loadConfigs();
      }
    } catch { setNotice('保存失败'); }
  };

  // Delete config
  const handleDelete = async (platform: string) => {
    try {
      await fetch(`/platform/channels/${platform}`, { method: 'DELETE', headers: authHeaders() });
      setNotice(`${platform} 渠道已删除`);
      loadConfigs();
    } catch { setNotice('删除失败'); }
  };

  // Toggle enabled
  const handleToggle = async (cfg: ChannelConfig) => {
    await handleSave({ ...cfg, enabled: !cfg.enabled });
  };

  return (
    <section className="space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-[34px] font-semibold leading-tight text-warm-900">IM 渠道接入</h2>
          <p className="mt-1 text-sm text-warm-500">将 Agent 接入飞书、企业微信等即时通讯平台，让用户在 IM 中直接与 Agent 对话。</p>
        </div>
        <button className="btn-primary" onClick={() => { setShowAdd(true); setEditing(null); }}>
          + 添加渠道
        </button>
      </div>

      {/* Architecture overview */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {Object.entries(PLATFORM_INFO).map(([key, info]) => (
          <div
            key={key}
            className="card cursor-pointer hover:shadow-md transition-shadow"
            onClick={() => {
              const existing = configs.find((c) => c.platform === key);
              if (existing) {
                setEditing({ ...existing });
                setShowAdd(false);
              } else {
                setNewPlatform(key);
                setEditing(null);
                setShowAdd(true);
              }
            }}
          >
            <div className="flex items-center gap-3 mb-2">
              <span className="text-[24px]">{info.icon}</span>
              <div>
                <h3 className="font-semibold text-warm-900" style={{ color: info.color }}>{info.name}</h3>
                {configs.find((c) => c.platform === key)?.enabled && (
                  <span className="text-[10px] text-green-600">● 已连接</span>
                )}
              </div>
            </div>
            <p className="text-xs text-warm-500 line-clamp-3">{info.guide}</p>
          </div>
        ))}
      </div>

      {/* Config list */}
      {configs.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-warm-700">已配置的渠道</h3>
          {configs.map((cfg) => {
            const info = PLATFORM_INFO[cfg.platform];
            return (
              <div key={cfg.platform} className={`card flex items-center justify-between ${cfg.enabled ? 'border-l-4 border-l-green-400' : 'border-l-4 border-l-warm-200'}`}>
                <div className="flex items-center gap-3">
                  <span className="text-[20px]">{info?.icon || '[plug]'}</span>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-warm-800">{info?.name || cfg.platform}</span>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                        cfg.enabled ? 'bg-green-50 text-green-600' : 'bg-warm-100 text-warm-400'
                      }`}>
                        {cfg.enabled ? '启用' : '禁用'}
                      </span>
                    </div>
                    <div className="text-[10px] text-warm-400 mt-0.5">
                      Agent: {cfg.agent_id || 'default'} · Webhook: {cfg.webhook_url ? '已配置' : '未配置'}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button className="btn-ghost text-xs px-2 py-1" onClick={() => handleToggle(cfg)}>
                    {cfg.enabled ? '禁用' : '启用'}
                  </button>
                  <button className="btn-ghost text-xs px-2 py-1" onClick={() => { setEditing({ ...cfg }); setShowAdd(false); }}>
                    编辑
                  </button>
                  <button className="btn-ghost text-xs px-2 py-1 text-red-500" onClick={() => handleDelete(cfg.platform)}>
                    删除
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Add / Edit modal */}
      {(showAdd || editing) && (
        <ChannelConfigModal
          platform={editing?.platform || newPlatform}
          config={editing || {
            platform: newPlatform,
            webhook_url: '',
            verify_token: '',
            signing_secret: '',
            enabled: true,
            agent_id: '',
            bot_name: '',
          }}
          isNew={!editing}
          onSave={handleSave}
          onClose={() => { setShowAdd(false); setEditing(null); }}
        />
      )}
    </section>
  );
}

// ── Config Modal ──────────────────────────────────────────────────

function ChannelConfigModal({
  platform,
  config,
  isNew,
  onSave,
  onClose,
}: {
  platform: string;
  config: ChannelConfig;
  isNew: boolean;
  onSave: (cfg: ChannelConfig) => void;
  onClose: () => void;
}): JSX.Element {
  const [form, setForm] = useState<ChannelConfig>(config);
  const info = PLATFORM_INFO[platform];

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    onSave(form);
  };

  const webhookHint = platform === 'feishu'
    ? 'https://open.feishu.cn/open-apis/bot/v2/hook/xxx'
    : 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx';

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 px-4 pt-[10vh] pb-8 overflow-y-auto" onClick={onClose}>
      <div className="w-full max-w-lg rounded-2xl bg-warm-100 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-warm-150 px-6 py-4">
          <div className="flex items-center gap-3">
            <span className="text-[24px]">{info?.icon || '[plug]'}</span>
            <div>
              <h3 className="text-lg font-semibold text-warm-900">
                {isNew ? '添加' : '编辑'} {info?.name || platform} 渠道
              </h3>
              <p className="text-xs text-warm-400">{info?.guide}</p>
            </div>
          </div>
          <button className="rounded-lg px-3 py-1.5 text-sm text-warm-500 hover:bg-warm-100" onClick={onClose}>关闭</button>
        </div>

        <form className="px-6 py-4 space-y-4" onSubmit={handleSubmit}>
          {/* Bot name */}
          <div>
            <label className="block text-sm font-medium text-warm-600 mb-1">Bot 名称</label>
            <input
              className="input-field w-full"
              placeholder="我的 Agent 助手"
              value={form.bot_name}
              onChange={(e) => setForm({ ...form, bot_name: e.target.value })}
            />
          </div>

          {/* Agent ID */}
          <div>
            <label className="block text-sm font-medium text-warm-600 mb-1">默认 Agent ID</label>
            <input
              className="input-field w-full"
              placeholder="输入 Agent ID（留空使用默认）"
              value={form.agent_id}
              onChange={(e) => setForm({ ...form, agent_id: e.target.value })}
            />
          </div>

          {/* Webhook URL */}
          <div>
            <label className="block text-sm font-medium text-warm-600 mb-1">Webhook URL</label>
            <input
              className="input-field w-full font-mono text-xs"
              placeholder={webhookHint}
              value={form.webhook_url}
              onChange={(e) => setForm({ ...form, webhook_url: e.target.value })}
            />
          </div>

          {/* Verify token */}
          <div>
            <label className="block text-sm font-medium text-warm-600 mb-1">验证 Token</label>
            <input
              className="input-field w-full"
              type="password"
              placeholder="平台提供的验证 token"
              value={form.verify_token}
              onChange={(e) => setForm({ ...form, verify_token: e.target.value })}
            />
          </div>

          {/* Signing secret */}
          <div>
            <label className="block text-sm font-medium text-warm-600 mb-1">签名密钥</label>
            <input
              className="input-field w-full"
              type="password"
              placeholder="Webhook 签名验证密钥"
              value={form.signing_secret}
              onChange={(e) => setForm({ ...form, signing_secret: e.target.value })}
            />
          </div>

          {/* Enabled toggle */}
          <label className="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
            />
            <span className="text-sm text-warm-700">启用此渠道</span>
          </label>

          {/* Webhook endpoint hint */}
          <div className="rounded-lg bg-warm-50 border border-warm-200 px-3 py-2 text-xs text-warm-600 font-mono">
            Webhook 接收端点：<code className="text-primary-600">{window.location.origin}/platform/channels/{platform}/webhook</code>
          </div>

          <div className="flex justify-end gap-2 pt-2 border-t border-warm-150">
            <button type="button" className="btn-secondary" onClick={onClose}>取消</button>
            <button type="submit" className="btn-primary">{isNew ? '添加' : '保存'}</button>
          </div>
        </form>
      </div>
    </div>
  );
}
