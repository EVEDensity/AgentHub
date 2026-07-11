// MCP Server Manager (P1-2)
// Management UI for MCP (Model Context Protocol) server connections.
// Displays configured servers, enables tool discovery, and provides
// tool execution testing.
// Sub-components: ServerCard, ToolExplorer, AddServerModal, CallResultViewer

import { useState, useEffect, type JSX } from 'react';
import { useMCPStore } from '../../stores/mcpStore';
import type { MCPServerConfig, MCPToolInfo } from '../../types';

// ── Transport Badge ───────────────────────────────────────────────────

function TransportBadge({ transport }: { transport: MCPServerConfig['transport'] }): JSX.Element {
  const isSSE = transport === 'sse';
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ${
      isSSE ? 'bg-primary-100 text-primary-700' : 'bg-primary-100 text-primary-700'
    }`}>
      <span className="material-symbols-outlined text-[12px]">{isSSE ? 'cloud_sync' : 'terminal'}</span>
      {transport.toUpperCase()}
    </span>
  );
}

// ── Status Badge ──────────────────────────────────────────────────────

function StatusBadge({ status, errorMessage }: { status: MCPServerConfig['status']; errorMessage?: string }): JSX.Element {
  const config: Record<string, { icon: string; label: string; cls: string }> = {
    connected: { icon: 'check_circle', label: '已连接', cls: 'bg-success-100 text-success-700' },
    disconnected: { icon: 'cancel', label: '已断开', cls: 'bg-warm-100 text-warm-600' },
    error: { icon: 'error', label: '错误', cls: 'bg-danger-100 text-danger-700' },
    unknown: { icon: 'help', label: '未知', cls: 'bg-warm-100 text-warm-500' },
  };
  const info = config[status] || config.unknown;
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ${info.cls}`} title={errorMessage}>
      <span className="material-symbols-outlined text-[12px]">{info.icon}</span>
      {info.label}
    </span>
  );
}

// ── Server Card ───────────────────────────────────────────────────────

function ServerCard({ server, isSelected, onSelect, onRemove, onTest }: {
  server: MCPServerConfig;
  isSelected: boolean;
  onSelect: () => void;
  onRemove: () => void;
  onTest: () => void;
}): JSX.Element {
  return (
    <div
      className={`rounded-xl border px-4 py-3 cursor-pointer transition-all ${
        isSelected
          ? 'border-primary-400 bg-primary-50 ring-1 ring-primary-200'
          : 'border-warm-200 bg-warm-100 hover:border-primary-300 hover:shadow-sm'
      }`}
      onClick={onSelect}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0 flex items-center gap-2">
          <span className="material-symbols-outlined text-lg text-warm-500 shrink-0">hub</span>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="text-sm font-semibold text-warm-900 truncate">{server.name}</span>
              <TransportBadge transport={server.transport} />
              <StatusBadge status={server.status} errorMessage={server.errorMessage} />
            </div>
            <p className="text-xs text-warm-500 truncate mt-0.5">{server.description}</p>
            {server.url && <p className="text-[10px] text-warm-400 font-mono truncate">{server.url}</p>}
            {server.command && <p className="text-[10px] text-warm-400 font-mono truncate">{server.command} {server.args?.join(' ')}</p>}
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0" onClick={e => e.stopPropagation()}>
          <button className="btn-ghost px-2 py-1 text-xs" onClick={onTest} title="测试连接">
            <span className="material-symbols-outlined text-[14px]">network_ping</span>
          </button>
          <button className="btn-ghost px-2 py-1 text-xs text-danger-500" onClick={onRemove} title="移除">
            <span className="material-symbols-outlined text-[14px]">delete</span>
          </button>
        </div>
      </div>
      {server.tags && server.tags.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-2">
          {server.tags.map(tag => (
            <span key={tag} className="rounded bg-warm-100 px-1.5 py-0.5 text-[9px] text-warm-500">{tag}</span>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Tool Card ─────────────────────────────────────────────────────────

function ToolCard({ tool, onExecute }: { tool: MCPToolInfo; onExecute: () => void }): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const hasParams = tool.inputSchema.properties && Object.keys(tool.inputSchema.properties).length > 0;

  return (
    <div className="rounded-lg border border-warm-200 bg-warm-100 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="material-symbols-outlined text-[16px] text-warm-400">build</span>
            <span className="text-sm font-medium text-warm-800 font-mono">{tool.name}</span>
          </div>
          <p className="text-xs text-warm-500 mt-0.5">{tool.description}</p>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {hasParams && (
            <button className="btn-ghost px-1.5 py-0.5 text-[10px]" onClick={() => setExpanded(!expanded)} title="查看参数">
              <span className="material-symbols-outlined text-[12px]">{expanded ? 'expand_less' : 'expand_more'}</span>
            </button>
          )}
          <button className="btn-primary px-2 py-1 text-[10px]" onClick={onExecute}>执行</button>
        </div>
      </div>
      {expanded && hasParams && (
        <div className="mt-2 pt-2 border-t border-warm-100">
          <p className="text-[10px] font-medium text-warm-600 mb-1">参数:</p>
          {tool.inputSchema.required && tool.inputSchema.required.length > 0 && (
            <p className="text-[10px] text-warm-400 mb-1">必填: {tool.inputSchema.required.join(', ')}</p>
          )}
          {Object.entries(tool.inputSchema.properties!).map(([key, prop]) => (
            <div key={key} className="flex items-baseline gap-1 text-[10px] ml-2">
              <span className="font-mono text-warm-700">{key}</span>
              <span className="text-warm-400">({prop.type})</span>
              {prop.description && <span className="text-warm-400">— {prop.description}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Add Server Modal ──────────────────────────────────────────────────

function AddServerModal({ visible, onClose, onAdd }: {
  visible: boolean;
  onClose: () => void;
  onAdd: (config: Omit<MCPServerConfig, 'id' | 'createdAt' | 'updatedAt' | 'status'>) => void;
}): JSX.Element | null {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [transport, setTransport] = useState<'stdio' | 'sse'>('sse');
  const [url, setUrl] = useState('');
  const [command, setCommand] = useState('');
  const [args, setArgs] = useState('');

  if (!visible) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;

    onAdd({
      name: name.trim(),
      description: description.trim(),
      transport,
      ...(transport === 'sse' ? { url: url.trim() || 'http://127.0.0.1:8099/mcp' } : {}),
      ...(transport === 'stdio' ? {
        command: command.trim(),
        args: args.trim() ? args.split(/\s+/) : [],
      } : {}),
    });

    // Reset form
    setName('');
    setDescription('');
    setTransport('sse');
    setUrl('');
    setCommand('');
    setArgs('');
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div className="bg-warm-100 rounded-2xl shadow-card-elevated p-6 w-full max-w-md mx-4" onClick={e => e.stopPropagation()}>
        <h3 className="text-lg font-semibold text-warm-900">添加 MCP 服务器</h3>
        <form onSubmit={handleSubmit} className="mt-4 space-y-3">
          <div>
            <label className="text-xs font-medium text-warm-600">名称 *</label>
            <input className="input mt-1" value={name} onChange={e => setName(e.target.value)} placeholder="My MCP Server" required />
          </div>
          <div>
            <label className="text-xs font-medium text-warm-600">描述</label>
            <input className="input mt-1" value={description} onChange={e => setDescription(e.target.value)} placeholder="简短描述此服务器的功能" />
          </div>
          <div>
            <label className="text-xs font-medium text-warm-600">传输方式</label>
            <div className="flex gap-2 mt-1">
              {(['sse', 'stdio'] as const).map(t => (
                <button
                  key={t}
                  type="button"
                  className={`flex-1 rounded-lg border px-3 py-2 text-sm font-medium transition-all ${
                    transport === t ? 'border-primary-400 bg-primary-50 text-primary-700' : 'border-warm-200 text-warm-500'
                  }`}
                  onClick={() => setTransport(t)}
                >
                  {t === 'sse' ? '[globe] SSE (HTTP)' : '[laptop] STDIO'}
                </button>
              ))}
            </div>
          </div>
          {transport === 'sse' ? (
            <div>
              <label className="text-xs font-medium text-warm-600">URL</label>
              <input className="input mt-1 font-mono text-sm" value={url} onChange={e => setUrl(e.target.value)} placeholder="http://127.0.0.1:8099/mcp" />
            </div>
          ) : (
            <>
              <div>
                <label className="text-xs font-medium text-warm-600">命令 *</label>
                <input className="input mt-1 font-mono text-sm" value={command} onChange={e => setCommand(e.target.value)} placeholder="node" />
              </div>
              <div>
                <label className="text-xs font-medium text-warm-600">参数</label>
                <input className="input mt-1 font-mono text-sm" value={args} onChange={e => setArgs(e.target.value)} placeholder="server.js --port 3000" />
              </div>
            </>
          )}
          <div className="flex gap-2 pt-2">
            <button type="button" className="btn-secondary flex-1" onClick={onClose}>取消</button>
            <button type="submit" className="btn-primary flex-1">添加</button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ── Call Result Viewer ────────────────────────────────────────────────

function CallResultViewer({ result, isExecuting }: { result: typeof useMCPStore.prototype.callResult; isExecuting: boolean }): JSX.Element | null {
  if (isExecuting) {
    return (
      <div className="rounded-lg border border-primary-200 bg-primary-50 px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-primary-500 animate-spin text-sm">progress_activity</span>
          <span className="text-sm text-primary-700">执行中...</span>
        </div>
      </div>
    );
  }
  if (!result) return null;
  return (
    <div className="rounded-lg border border-warm-200 bg-warm-50 px-4 py-3">
      <div className="flex items-center gap-2 mb-2">
        <span className={`material-symbols-outlined text-sm ${result.isError ? 'text-danger-500' : 'text-success-500'}`}>
          {result.isError ? 'error' : 'check_circle'}
        </span>
        <span className="text-sm font-medium text-warm-700">执行结果</span>
      </div>
      {(result.content as Array<{type: string; text?: string; data?: string; mimeType?: string}>).map((c, i) => (
        <div key={i} className="mt-1">
          {c.type === 'text' && (
            <pre className="text-xs text-warm-700 whitespace-pre-wrap font-mono bg-warm-100 rounded p-2 border border-warm-100 max-h-60 overflow-auto">{c.text}</pre>
          )}
          {c.type === 'image' && (
            <img src={`data:${c.mimeType};base64,${c.data}`} alt="Result" className="max-w-full rounded" />
          )}
        </div>
      ))}
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────

export default function MCPServerManager(): JSX.Element {
  const {
    servers, selectedServerId, tools, resources, prompts, callResult,
    isLoading, isExecuting, demoMode,
    loadServers, selectServer, discoverTools, discoverResources, discoverPrompts,
    callTool, addServer, removeServer, testConnection,
  } = useMCPStore();

  const [showAddModal, setShowAddModal] = useState(false);
  const [activeTab, setActiveTab] = useState<'tools' | 'resources' | 'prompts'>('tools');

  useEffect(() => {
    loadServers();
  }, [loadServers]);

  const selectedServer = servers.find(s => s.id === selectedServerId);

  return (
    <section className="space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-[34px] font-semibold leading-tight text-warm-900">MCP Gateway</h2>
          <p className="mt-1 text-sm text-warm-500">管理 Model Context Protocol 服务器连接，发现和测试工具。</p>
          {demoMode && (
            <p className="mt-1 text-xs text-warning-600 flex items-center gap-1">
              <span className="material-symbols-outlined text-[12px]">info</span>
              Demo 模式 — 显示示例数据，连接后端获取实际 MCP 工具
            </p>
          )}
        </div>
        <button className="btn-primary" onClick={() => setShowAddModal(true)}>
          <span className="material-symbols-outlined text-[16px] align-middle">add</span> 添加服务器
        </button>
      </div>

      {/* MCP Protocol Info Banner */}
      <div className="rounded-xl border border-primary-200 bg-primary-50 px-4 py-3">
        <div className="flex items-start gap-2">
          <span className="material-symbols-outlined text-primary-500 mt-0.5 text-lg">info</span>
          <div>
            <p className="text-sm font-medium text-primary-800">关于 MCP (Model Context Protocol)</p>
            <p className="mt-0.5 text-sm text-primary-600">
              MCP 是 Anthropic 发布的开放协议，允许 AI 应用安全地访问本地和远程工具、资源和提示模板。
              AgentHub MCP Gateway 支持 <strong>STDIO</strong>（本地进程）和 <strong>SSE</strong>（HTTP 流）两种传输方式，
              将平台的知识搜索、Agent 调用、工作流管理等能力暴露为 MCP 工具。
            </p>
          </div>
        </div>
      </div>

      {/* Server List + Detail */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        {/* Left: Server List */}
        <div className="lg:col-span-2 space-y-2">
          <h3 className="text-sm font-semibold text-warm-700">服务器列表 ({servers.length})</h3>
          {isLoading ? (
            <div className="text-sm text-warm-400 py-8 text-center">加载中...</div>
          ) : servers.length === 0 ? (
            <div className="text-sm text-warm-400 py-8 text-center">
              <span className="material-symbols-outlined text-3xl mb-2 block">hub</span>
              暂无 MCP 服务器
            </div>
          ) : (
            servers.map(s => (
              <ServerCard
                key={s.id}
                server={s}
                isSelected={selectedServerId === s.id}
                onSelect={() => selectServer(s.id)}
                onRemove={() => removeServer(s.id)}
                onTest={() => testConnection(s.id)}
              />
            ))
          )}
        </div>

        {/* Right: Server Detail */}
        <div className="lg:col-span-3">
          {!selectedServer ? (
            <div className="rounded-xl border border-dashed border-warm-300 bg-warm-50 px-6 py-16 text-center">
              <span className="material-symbols-outlined text-4xl text-warm-300 mb-2 block">arrow_back</span>
              <p className="text-sm text-warm-500">选择一个服务器以查看其工具和资源</p>
            </div>
          ) : (
            <div className="space-y-3">
              {/* Server Info */}
              <div className="rounded-xl border border-warm-200 bg-warm-100 px-4 py-3">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="flex items-center gap-2">
                      <h4 className="font-semibold text-warm-900">{selectedServer!.name}</h4>
                      <TransportBadge transport={selectedServer!.transport} />
                      <StatusBadge status={selectedServer!.status} errorMessage={selectedServer!.errorMessage} />
                    </div>
                    <p className="text-xs text-warm-500 mt-0.5">{selectedServer!.description}</p>
                  </div>
                  <div className="flex items-center gap-1">
                    <button className="btn-ghost px-2 py-1 text-xs" onClick={() => testConnection(selectedServer!.id)}>
                      <span className="material-symbols-outlined text-[14px]">refresh</span> 刷新
                    </button>
                  </div>
                </div>
              </div>

              {/* Tab Bar */}
              <div className="flex gap-1 border-b border-warm-200">
                {(['tools', 'resources', 'prompts'] as const).map(tab => (
                  <button
                    key={tab}
                    className={`px-3 py-2 text-sm font-medium border-b-2 transition-colors ${
                      activeTab === tab
                        ? 'border-primary-500 text-primary-700'
                        : 'border-transparent text-warm-500 hover:text-warm-700'
                    }`}
                    onClick={() => setActiveTab(tab)}
                  >
                    {tab === 'tools' ? '[wrench] 工具' : tab === 'resources' ? '[package] 资源' : '[chat] 提示模板'}
                    {tab === 'tools' && tools.length > 0 && <span className="ml-1 text-xs">({tools.length})</span>}
                    {tab === 'resources' && resources.length > 0 && <span className="ml-1 text-xs">({resources.length})</span>}
                    {tab === 'prompts' && prompts.length > 0 && <span className="ml-1 text-xs">({prompts.length})</span>}
                  </button>
                ))}
              </div>

              {/* Tab Content */}
              {activeTab === 'tools' && (
                <div className="space-y-2">
                  {tools.length === 0 ? (
                    <p className="text-xs text-warm-400 py-4 text-center">未发现工具</p>
                  ) : (
                    tools.map(tool => (
                      <ToolCard
                        key={tool.name}
                        tool={tool}
                        onExecute={() => {
                          const args: Record<string, unknown> = {};
                          if (tool.inputSchema.properties) {
                            for (const [key, prop] of Object.entries(tool.inputSchema.properties)) {
                              if (prop.default !== undefined) {
                                args[key] = prop.default;
                              } else if (prop.type === 'string') {
                                args[key] = '';
                              }
                            }
                          }
                          callTool(selectedServer!.id, tool.name, args);
                        }}
                      />
                    ))
                  )}
                </div>
              )}

              {activeTab === 'resources' && (
                <div className="space-y-2">
                  {resources.length === 0 ? (
                    <p className="text-xs text-warm-400 py-4 text-center">未发现资源</p>
                  ) : (
                    resources.map(r => (
                      <div key={r.uri} className="rounded-lg border border-warm-200 bg-warm-100 p-3">
                        <div className="flex items-center gap-2">
                          <span className="material-symbols-outlined text-[16px] text-warm-400">description</span>
                          <span className="text-sm font-medium text-warm-800 font-mono truncate">{r.uri}</span>
                        </div>
                        <p className="text-xs text-warm-500 mt-0.5 ml-6">{r.name} — {r.description}</p>
                      </div>
                    ))
                  )}
                </div>
              )}

              {activeTab === 'prompts' && (
                <div className="space-y-2">
                  {prompts.length === 0 ? (
                    <p className="text-xs text-warm-400 py-4 text-center">未发现提示模板</p>
                  ) : (
                    prompts.map(p => (
                      <div key={p.name} className="rounded-lg border border-warm-200 bg-warm-100 p-3">
                        <div className="flex items-center gap-2">
                          <span className="material-symbols-outlined text-[16px] text-warm-400">chat</span>
                          <span className="text-sm font-medium text-warm-800 font-mono">{p.name}</span>
                        </div>
                        <p className="text-xs text-warm-500 mt-0.5 ml-6">{p.description}</p>
                      </div>
                    ))
                  )}
                </div>
              )}

              {/* Call Result */}
              <CallResultViewer result={callResult} isExecuting={isExecuting} />
            </div>
          )}
        </div>
      </div>

      {/* Add Modal */}
      <AddServerModal
        visible={showAddModal}
        onClose={() => setShowAddModal(false)}
        onAdd={(config) => { addServer(config); }}
      />
    </section>
  );
}
