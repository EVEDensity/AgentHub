'use client';

import { type JSX } from 'react';

// ── Types ─────────────────────────────────────────────────────────────

interface CodeConfig {
  language?: string;
  code?: string;
  timeout?: number;
}

interface HttpConfig {
  method?: string;
  url?: string;
  headers?: string;
  body?: string;
  timeout?: number;
  retry?: number;
}

interface KnowledgeConfig {
  collectionId?: string;
  query?: string;
  topK?: number;
  scoreThreshold?: number;
}

interface HumanConfig {
  prompt?: string;
  assignee?: string;
  timeout?: number;
}

interface NodeConfig {
  id: string;
  type: string;
  codeConfig?: CodeConfig;
  httpConfig?: HttpConfig;
  knowledgeConfig?: KnowledgeConfig;
  humanConfig?: HumanConfig;
}

interface Props {
  node: NodeConfig;
  onPatch: (id: string, patch: Record<string, unknown>) => void;
}

// ── Sub-components ────────────────────────────────────────────────────

function CodeNodeConfig({ cfg, onPatch, nodeId }: { cfg: CodeConfig; onPatch: (p: Record<string, unknown>) => void; nodeId: string }): JSX.Element {
  return (
    <div className="rounded-xl border border-primary-100 bg-primary-50/30 p-4 space-y-3">
      <div className="flex items-center gap-1.5 text-[11px] font-semibold text-primary-600">
        <span className="material-symbols-outlined text-[14px]">code</span>代码配置
      </div>
      <div className="space-y-1.5">
        <label className="text-[10px] text-warm-400">语言</label>
        <select
          className="input-field text-sm"
          value={cfg.language || 'python'}
          onChange={(e) => onPatch({ codeConfig: { ...cfg, language: e.target.value } })}
        >
          <option value="python">Python</option>
          <option value="javascript">JavaScript</option>
          <option value="bash">Bash</option>
          <option value="sql">SQL</option>
        </select>
      </div>
      <div className="space-y-1.5">
        <label className="text-[10px] text-warm-400">
          代码 <span className="text-primary-400">{'{{variable}}'} 支持</span>
        </label>
        <textarea
          className="input-field min-h-[80px] resize-y text-xs font-mono"
          value={cfg.code || ''}
          onChange={(e) => onPatch({ codeConfig: { ...cfg, code: e.target.value } })}
          placeholder={'# Use {{node_id.output}} to reference upstream nodes\nprint("Hello")'}
        />
      </div>
      <div className="space-y-1.5">
        <label className="text-[10px] text-warm-400">超时 (ms)</label>
        <input
          className="input-field text-sm"
          type="number"
          value={cfg.timeout || 30000}
          onChange={(e) => onPatch({ codeConfig: { ...cfg, timeout: parseInt(e.target.value) || 30000 } })}
        />
      </div>
    </div>
  );
}

function HttpNodeConfig({ cfg, onPatch }: { cfg: HttpConfig; onPatch: (p: Record<string, unknown>) => void }): JSX.Element {
  return (
    <div className="rounded-xl border border-pink-100 bg-pink-50/30 p-4 space-y-3">
      <div className="flex items-center gap-1.5 text-[11px] font-semibold" style={{ color: '#EC4899' }}>
        <span className="material-symbols-outlined text-[14px]">http</span>HTTP 配置
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1.5">
          <label className="text-[10px] text-warm-400">方法</label>
          <select
            className="input-field text-sm"
            value={cfg.method || 'GET'}
            onChange={(e) => onPatch({ httpConfig: { ...cfg, method: e.target.value } })}
          >
            <option>GET</option><option>POST</option><option>PUT</option>
            <option>PATCH</option><option>DELETE</option>
          </select>
        </div>
        <div className="space-y-1.5">
          <label className="text-[10px] text-warm-400">重试</label>
          <input
            className="input-field text-sm" type="number" min="0" max="5"
            value={cfg.retry || 1}
            onChange={(e) => onPatch({ httpConfig: { ...cfg, retry: parseInt(e.target.value) || 0 } })}
          />
        </div>
      </div>
      <div className="space-y-1.5">
        <label className="text-[10px] text-warm-400">URL <span className="text-primary-400">{'{{variable}}'} 支持</span></label>
        <input
          className="input-field text-sm font-mono"
          value={cfg.url || ''}
          onChange={(e) => onPatch({ httpConfig: { ...cfg, url: e.target.value } })}
          placeholder="https://api.example.com/data"
        />
      </div>
      <div className="space-y-1.5">
        <label className="text-[10px] text-warm-400">Headers (JSON)</label>
        <textarea
          className="input-field min-h-[40px] resize-y text-xs font-mono"
          value={cfg.headers || ''}
          onChange={(e) => onPatch({ httpConfig: { ...cfg, headers: e.target.value } })}
          placeholder='{"Authorization":"Bearer {{auth.output}}"}'
        />
      </div>
      <div className="space-y-1.5">
        <label className="text-[10px] text-warm-400">Body <span className="text-primary-400">{'{{variable}}'} 支持</span></label>
        <textarea
          className="input-field min-h-[50px] resize-y text-xs font-mono"
          value={cfg.body || ''}
          onChange={(e) => onPatch({ httpConfig: { ...cfg, body: e.target.value } })}
          placeholder='{"query":"{{knowledge.output}}"}'
        />
      </div>
      <div className="space-y-1.5">
        <label className="text-[10px] text-warm-400">超时 (ms)</label>
        <input
          className="input-field text-sm" type="number"
          value={cfg.timeout || 10000}
          onChange={(e) => onPatch({ httpConfig: { ...cfg, timeout: parseInt(e.target.value) || 10000 } })}
        />
      </div>
    </div>
  );
}

function KnowledgeNodeConfig({ cfg, onPatch }: { cfg: KnowledgeConfig; onPatch: (p: Record<string, unknown>) => void }): JSX.Element {
  return (
    <div className="rounded-xl border border-teal-100 bg-teal-50/30 p-4 space-y-3">
      <div className="flex items-center gap-1.5 text-[11px] font-semibold" style={{ color: '#14B8A6' }}>
        <span className="material-symbols-outlined text-[14px]">book_5</span>知识库配置
      </div>
      <div className="space-y-1.5">
        <label className="text-[10px] text-warm-400">知识库 ID</label>
        <input
          className="input-field text-sm"
          value={cfg.collectionId || ''}
          onChange={(e) => onPatch({ knowledgeConfig: { ...cfg, collectionId: e.target.value } })}
          placeholder="kb-project-docs"
        />
      </div>
      <div className="space-y-1.5">
        <label className="text-[10px] text-warm-400">查询 <span className="text-primary-400">{'{{variable}}'} 支持</span></label>
        <textarea
          className="input-field min-h-[50px] resize-y text-xs"
          value={cfg.query || ''}
          onChange={(e) => onPatch({ knowledgeConfig: { ...cfg, query: e.target.value } })}
          placeholder="{{orchestrator.output}}"
        />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1.5">
          <label className="text-[10px] text-warm-400">Top-K</label>
          <input
            className="input-field text-sm" type="number" min="1" max="20"
            value={cfg.topK || 5}
            onChange={(e) => onPatch({ knowledgeConfig: { ...cfg, topK: parseInt(e.target.value) || 5 } })}
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-[10px] text-warm-400">分数阈值</label>
          <input
            className="input-field text-sm" type="number" min="0" max="1" step="0.05"
            value={cfg.scoreThreshold || 0.6}
            onChange={(e) => onPatch({ knowledgeConfig: { ...cfg, scoreThreshold: parseFloat(e.target.value) || 0.6 } })}
          />
        </div>
      </div>
    </div>
  );
}

function HumanNodeConfig({ cfg, onPatch }: { cfg: HumanConfig; onPatch: (p: Record<string, unknown>) => void }): JSX.Element {
  return (
    <div className="rounded-xl border border-amber-100 bg-amber-50/30 p-4 space-y-3">
      <div className="flex items-center gap-1.5 text-[11px] font-semibold text-amber-600">
        <span className="material-symbols-outlined text-[14px]">person_check</span>人工审批配置
      </div>
      <div className="space-y-1.5">
        <label className="text-[10px] text-warm-400">审批提示 <span className="text-primary-400">{'{{variable}}'} 支持</span></label>
        <textarea
          className="input-field min-h-[50px] resize-y text-xs"
          value={cfg.prompt || ''}
          onChange={(e) => onPatch({ humanConfig: { ...cfg, prompt: e.target.value } })}
          placeholder="请审核以下代码变更..."
        />
      </div>
      <div className="space-y-1.5">
        <label className="text-[10px] text-warm-400">指派用户/角色</label>
        <input
          className="input-field text-sm"
          value={cfg.assignee || ''}
          onChange={(e) => onPatch({ humanConfig: { ...cfg, assignee: e.target.value } })}
          placeholder="admin 或留空（任意管理员）"
        />
      </div>
      <div className="space-y-1.5">
        <label className="text-[10px] text-warm-400">超时 (秒, 0=无限)</label>
        <input
          className="input-field text-sm" type="number" min="0"
          value={cfg.timeout || 3600}
          onChange={(e) => onPatch({ humanConfig: { ...cfg, timeout: parseInt(e.target.value) || 0 } })}
        />
      </div>
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────

export default function NodeConfigPanel({ node, onPatch }: Props): JSX.Element | null {
  const patch = (p: Record<string, unknown>) => onPatch(node.id, p);

  switch (node.type) {
    case 'code':
      return <CodeNodeConfig cfg={node.codeConfig || {}} onPatch={patch} nodeId={node.id} />;
    case 'http':
      return <HttpNodeConfig cfg={node.httpConfig || {}} onPatch={patch} />;
    case 'knowledge':
      return <KnowledgeNodeConfig cfg={node.knowledgeConfig || {}} onPatch={patch} />;
    case 'human':
      return <HumanNodeConfig cfg={node.humanConfig || {}} onPatch={patch} />;
    default:
      return null;
  }
}
