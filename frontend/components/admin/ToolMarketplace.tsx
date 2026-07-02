'use client';

import { useState, useEffect, type JSX } from 'react';
import { useToolStore, TOOL_CATEGORIES, type ToolDefinition } from '../../stores/toolStore';

// ── Sub-components ───────────────────────────────────────────────

function ToolCard({
  tool,
  onToggle,
  onView,
  onDelete,
}: {
  tool: ToolDefinition;
  onToggle: () => void;
  onView: () => void;
  onDelete: () => void;
}): JSX.Element {
  const cat = TOOL_CATEGORIES[tool.category] || { label: tool.category, icon: 'build' };
  const riskColors: Record<string, string> = {
    L1: 'bg-green-100 text-green-700',
    L2: 'bg-yellow-100 text-yellow-700',
    L3: 'bg-red-100 text-red-700',
  };

  return (
    <div className="card group relative overflow-hidden transition-all duration-200 hover:shadow-lg hover:-translate-y-0.5">
      {/* Header */}
      <div className="flex items-start gap-3">
        <span
          className={`material-symbols-outlined text-[20px] shrink-0 mt-0.5 ${
            tool.enabled ? 'text-primary-500' : 'text-warm-300'
          }`}
        >
          {cat.icon}
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h4 className="text-sm font-semibold text-warm-900 truncate">{tool.name}</h4>
            <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${riskColors[tool.riskLevel] || 'bg-warm-100 text-warm-500'}`}>
              {tool.riskLevel}
            </span>
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-warm-100 text-warm-500">
              {cat.label}
            </span>
            {tool.handlerType === 'builtin' && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-50 text-blue-600">内置</span>
            )}
            {tool.handlerType === 'custom' && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-50 text-purple-600">自定义</span>
            )}
            {tool.handlerType === 'community' && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-600">社区</span>
            )}
          </div>
          <p className="text-xs text-warm-500 mt-1 line-clamp-2">{tool.description}</p>
        </div>
      </div>

      {/* Parameters preview */}
      {tool.parameters && tool.parameters.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {tool.parameters.slice(0, 4).map((p) => (
            <span key={p.name} className="text-[10px] px-1.5 py-0.5 rounded bg-warm-50 text-warm-600 border border-warm-100">
              {p.name}{p.required ? '*' : ''}: {p.type}
            </span>
          ))}
          {tool.parameters.length > 4 && (
            <span className="text-[10px] text-warm-400">+{tool.parameters.length - 4} more</span>
          )}
        </div>
      )}

      {/* Actions */}
      <div className="mt-3 flex items-center gap-2 pt-2 border-t border-warm-100">
        <button className="btn-ghost text-[11px] px-2 py-1" onClick={onView}>
          <span className="material-symbols-outlined text-[12px]">info</span> 详情
        </button>
        <button
          className={`text-[11px] px-2 py-1 rounded transition-colors ${
            tool.enabled
              ? 'text-green-600 hover:bg-green-50'
              : 'text-warm-400 hover:bg-warm-100'
          }`}
          onClick={onToggle}
        >
          <span className="material-symbols-outlined text-[12px]">
            {tool.enabled ? 'toggle_on' : 'toggle_off'}
          </span>
          {tool.enabled ? ' 已启用' : ' 已禁用'}
        </button>
        {tool.handlerType !== 'builtin' && (
          <button className="btn-ghost text-[11px] px-2 py-1 text-red-500" onClick={onDelete}>
            <span className="material-symbols-outlined text-[12px]">delete</span>
          </button>
        )}
        <div className="flex-1" />
        {tool.isConcurrencySafe && (
          <span className="text-[10px] text-green-500" title="并发安全">∥</span>
        )}
        {tool.requiresUserConfirmation && (
          <span className="text-[10px] text-amber-500" title="需要用户确认">⚠</span>
        )}
      </div>
    </div>
  );
}

function ToolDetailModal({
  tool,
  onClose,
}: {
  tool: ToolDefinition | null;
  onClose: () => void;
}): JSX.Element | null {
  if (!tool) return null;
  const cat = TOOL_CATEGORIES[tool.category] || { label: tool.category, icon: 'build' };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 px-4 pt-[10vh] pb-8 overflow-y-auto" onClick={onClose}>
      <div className="w-full max-w-lg rounded-2xl bg-white shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-warm-150 px-6 py-4">
          <div className="flex items-center gap-3">
            <span className="material-symbols-outlined text-[24px] text-primary-500">{cat.icon}</span>
            <div>
              <h3 className="text-lg font-semibold text-warm-900">{tool.name}</h3>
              <p className="text-xs text-warm-500">{tool.id}</p>
            </div>
          </div>
          <button className="rounded-lg px-3 py-1.5 text-sm text-warm-500 hover:bg-warm-100" onClick={onClose}>关闭</button>
        </div>
        <div className="px-6 py-4 space-y-4">
          <p className="text-sm text-warm-600">{tool.description}</p>

          {/* Parameters */}
          <div>
            <h4 className="text-xs font-semibold text-warm-700 mb-2">参数定义</h4>
            <div className="space-y-2">
              {tool.parameters.map((p) => (
                <div key={p.name} className="rounded border border-warm-100 px-3 py-2 text-xs">
                  <div className="flex items-center gap-2">
                    <code className="text-primary-600 font-medium">{p.name}</code>
                    <span className="text-warm-400">{p.type}</span>
                    {p.required && <span className="text-red-500">*必填</span>}
                    {p.default !== undefined && <span className="text-warm-400">默认: {String(p.default)}</span>}
                  </div>
                  <p className="text-warm-500 mt-0.5">{p.description}</p>
                  {p.enum && (
                    <div className="mt-1 flex gap-1">
                      {p.enum.map((v) => (
                        <code key={v} className="text-[10px] bg-warm-50 px-1 rounded">{v}</code>
                      ))}
                    </div>
                  )}
                </div>
              ))}
              {tool.parameters.length === 0 && (
                <p className="text-xs text-warm-400">无参数</p>
              )}
            </div>
          </div>

          {/* Examples */}
          {tool.examples && tool.examples.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-warm-700 mb-2">使用示例</h4>
              {tool.examples.map((ex, i) => (
                <div key={i} className="rounded border border-warm-100 px-3 py-2 text-xs mb-2">
                  <p className="text-warm-600">💬 {ex.user_question}</p>
                  <pre className="mt-1 text-[10px] bg-warm-50 p-2 rounded overflow-x-auto">
                    {JSON.stringify(ex.parameters, null, 2)}
                  </pre>
                </div>
              ))}
            </div>
          )}

          {/* Meta */}
          <div className="flex flex-wrap gap-2 text-[10px] text-warm-400">
            <span>风险: {tool.riskLevel}</span>
            <span>·</span>
            <span>返回类型: {tool.returnType}</span>
            <span>·</span>
            <span>并发安全: {tool.isConcurrencySafe ? '是' : '否'}</span>
            <span>·</span>
            <span>需确认: {tool.requiresUserConfirmation ? '是' : '否'}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function SwaggerImportModal({
  visible,
  onClose,
  onImport,
}: {
  visible: boolean;
  onClose: () => void;
  onImport: (spec: object) => void;
}): JSX.Element | null {
  if (!visible) return null;
  const [text, setText] = useState('');
  const [file, setFile] = useState<File | null>(null);

  const handleImport = () => {
    if (file) {
      const reader = new FileReader();
      reader.onload = (e) => {
        try {
          const spec = JSON.parse(e.target?.result as string);
          onImport(spec);
        } catch { /* invalid JSON */ }
      };
      reader.readAsText(file);
    } else if (text.trim()) {
      try {
        const spec = JSON.parse(text);
        onImport(spec);
      } catch { /* invalid JSON */ }
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 px-4 pt-[10vh] pb-8 overflow-y-auto" onClick={onClose}>
      <div className="w-full max-w-lg rounded-2xl bg-white shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-warm-150 px-6 py-4">
          <h3 className="text-lg font-semibold text-warm-900">📥 导入 Swagger/OpenAPI</h3>
          <button className="rounded-lg px-3 py-1.5 text-sm text-warm-500 hover:bg-warm-100" onClick={onClose}>关闭</button>
        </div>
        <div className="px-6 py-4 space-y-4">
          <p className="text-sm text-warm-500">上传 OpenAPI 3.0 规范的 JSON 文件，或直接粘贴 JSON 内容，系统将自动解析 API 端点生成工具定义。</p>

          <div className="border-2 border-dashed border-warm-200 rounded-xl p-6 text-center">
            <input
              type="file"
              accept=".json,.yaml,.yml"
              className="hidden"
              id="swagger-file-input"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
            <label htmlFor="swagger-file-input" className="cursor-pointer">
              <span className="material-symbols-outlined text-[28px] text-warm-400">upload_file</span>
              <p className="text-sm text-warm-500 mt-1">
                {file ? file.name : '点击选择 OpenAPI JSON 文件'}
              </p>
            </label>
          </div>

          <div className="relative">
            <div className="absolute inset-0 flex items-center"><div className="w-full border-t border-warm-100" /></div>
            <div className="relative flex justify-center"><span className="bg-white px-2 text-xs text-warm-400">或粘贴 JSON</span></div>
          </div>

          <textarea
            className="input-field w-full text-xs font-mono"
            rows={8}
            placeholder='{"openapi": "3.0.0", "info": {...}, "paths": {...}}'
            value={text}
            onChange={(e) => setText(e.target.value)}
          />

          <button className="btn-primary w-full" onClick={handleImport} disabled={!file && !text.trim()}>
            解析并导入
          </button>
        </div>
      </div>
    </div>
  );
}

function ToolBindingModal({
  visible,
  agentId,
  onClose,
}: {
  visible: boolean;
  agentId: string;
  onClose: () => void;
}): JSX.Element | null {
  if (!visible) return null;
  const tools = useToolStore((s) => s.tools);
  const bindings = useToolStore((s) => s.agentBindings[agentId]) || [];
  const updateBindings = useToolStore((s) => s.updateAgentBindings);
  const [selected, setSelected] = useState<Set<string>>(new Set(bindings));

  const toggle = (toolId: string) => {
    const next = new Set(selected);
    if (next.has(toolId)) next.delete(toolId); else next.add(toolId);
    setSelected(next);
  };

  const handleSave = async () => {
    await updateBindings(agentId, [...selected]);
    onClose();
  };

  const grouped = tools.reduce<Record<string, ToolDefinition[]>>((acc, t) => {
    const c = t.category || 'other';
    if (!acc[c]) acc[c] = [];
    acc[c].push(t);
    return acc;
  }, {});

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 px-4 pt-[5vh] pb-8 overflow-y-auto" onClick={onClose}>
      <div className="w-full max-w-2xl rounded-2xl bg-white shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-warm-150 px-6 py-4">
          <div>
            <h3 className="text-lg font-semibold text-warm-900">🔧 工具绑定</h3>
            <p className="text-xs text-warm-500">Agent: {agentId} · 已选 {selected.size} 个工具</p>
          </div>
          <button className="rounded-lg px-3 py-1.5 text-sm text-warm-500 hover:bg-warm-100" onClick={onClose}>关闭</button>
        </div>
        <div className="px-6 py-4 space-y-4 max-h-[60vh] overflow-y-auto">
          {Object.entries(grouped).map(([cat, catTools]) => (
            <div key={cat}>
              <h4 className="text-xs font-semibold text-warm-600 mb-2 flex items-center gap-1.5">
                <span className="material-symbols-outlined text-[14px]">{TOOL_CATEGORIES[cat]?.icon || 'build'}</span>
                {TOOL_CATEGORIES[cat]?.label || cat}
              </h4>
              <div className="space-y-1">
                {catTools.map((t) => (
                  <label
                    key={t.id}
                    className={`flex items-center gap-3 p-2 rounded-lg cursor-pointer transition-colors ${
                      selected.has(t.id) ? 'bg-primary-50 border border-primary-200' : 'hover:bg-warm-50 border border-transparent'
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={selected.has(t.id)}
                      onChange={() => toggle(t.id)}
                      className="shrink-0"
                    />
                    <div className="flex-1 min-w-0">
                      <span className="text-sm font-medium text-warm-800">{t.name}</span>
                      <span className="text-xs text-warm-400 ml-2">{t.description.slice(0, 40)}...</span>
                    </div>
                    <span className={`text-[10px] px-1 py-0.5 rounded ${
                      t.riskLevel === 'L1' ? 'bg-green-50 text-green-600' :
                      t.riskLevel === 'L2' ? 'bg-yellow-50 text-yellow-600' :
                      'bg-red-50 text-red-600'
                    }`}>{t.riskLevel}</span>
                  </label>
                ))}
              </div>
            </div>
          ))}
        </div>
        <div className="border-t border-warm-150 px-6 py-4 flex justify-end gap-2">
          <button className="btn-secondary" onClick={onClose}>取消</button>
          <button className="btn-primary" onClick={handleSave}>保存绑定</button>
        </div>
      </div>
    </div>
  );
}

// ── Main Module ───────────────────────────────────────────────────

export default function ToolMarketplace(): JSX.Element {
  const store = useToolStore();
  const [detailTool, setDetailTool] = useState<ToolDefinition | null>(null);
  const [showSwaggerImport, setShowSwaggerImport] = useState(false);
  const [bindingAgentId, setBindingAgentId] = useState<string | null>(null);

  useEffect(() => {
    store.loadTools();
  }, []);

  const filtered = store.filteredTools();
  const categories = [...new Set(store.tools.map((t) => t.category))];

  const handleSwaggerImport = async (spec: object) => {
    const discovered = await store.importFromSwagger(spec);
    for (const tool of discovered) {
      await store.createTool(tool);
    }
    setShowSwaggerImport(false);
  };

  return (
    <section className="space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-[34px] font-semibold leading-tight text-warm-900">工具市场</h2>
          <p className="mt-1 text-sm text-warm-500">浏览、安装和管理 Agent 可用工具。已安装 {store.tools.filter((t) => t.enabled).length}/{store.tools.length} 个</p>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn-secondary" onClick={() => setShowSwaggerImport(true)}>
            📥 导入 Swagger
          </button>
          <button className="btn-primary" onClick={() => store.loadTools()}>
            🔄 刷新
          </button>
        </div>
      </div>

      {/* Search + Filters */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-sm">
          <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-[16px] text-warm-400">search</span>
          <input
            className="input-field pl-9 text-sm"
            placeholder="搜索工具..."
            value={store.searchQuery}
            onChange={(e) => store.setSearchQuery(e.target.value)}
          />
        </div>
        <div className="flex gap-1 flex-wrap">
          <button
            className={`text-xs px-3 py-1.5 rounded-full transition-colors ${
              !store.selectedCategory ? 'bg-primary-500 text-white' : 'bg-warm-100 text-warm-600 hover:bg-warm-200'
            }`}
            onClick={() => store.setSelectedCategory(null)}
          >
            全部
          </button>
          {categories.map((cat) => (
            <button
              key={cat}
              className={`text-xs px-3 py-1.5 rounded-full transition-colors ${
                store.selectedCategory === cat ? 'bg-primary-500 text-white' : 'bg-warm-100 text-warm-600 hover:bg-warm-200'
              }`}
              onClick={() => store.setSelectedCategory(cat)}
            >
              {TOOL_CATEGORIES[cat]?.label || cat}
            </button>
          ))}
        </div>
      </div>

      {/* Stats bar */}
      <div className="grid grid-cols-4 gap-3">
        {[
          { label: '总数', value: store.tools.length, icon: 'build', color: 'text-primary-500' },
          { label: '已启用', value: store.tools.filter((t) => t.enabled).length, icon: 'toggle_on', color: 'text-green-500' },
          { label: '内置', value: store.tools.filter((t) => t.handlerType === 'builtin').length, icon: 'lock', color: 'text-blue-500' },
          { label: '自定义', value: store.tools.filter((t) => t.handlerType !== 'builtin').length, icon: 'edit', color: 'text-purple-500' },
        ].map((stat) => (
          <div key={stat.label} className="card flex items-center gap-3">
            <span className={`material-symbols-outlined text-[20px] ${stat.color}`}>{stat.icon}</span>
            <div>
              <div className="text-sm font-semibold text-warm-900">{stat.value}</div>
              <div className="text-[10px] text-warm-400">{stat.label}</div>
            </div>
          </div>
        ))}
      </div>

      {/* Tool grid */}
      {store.loading ? (
        <div className="text-center py-12 text-warm-400">加载中...</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {filtered.map((tool) => (
            <ToolCard
              key={tool.id}
              tool={tool}
              onToggle={() => store.toggleToolEnabled(tool.id)}
              onView={() => setDetailTool(tool)}
              onDelete={() => store.deleteTool(tool.id)}
            />
          ))}
          {filtered.length === 0 && (
            <div className="col-span-full text-center py-12 text-warm-400">
              {store.searchQuery ? '没有匹配的工具' : '暂无工具，点击"导入 Swagger"添加'}
            </div>
          )}
        </div>
      )}

      {/* Agent bindings quick-access */}
      {bindingAgentId && (
        <ToolBindingModal
          visible={!!bindingAgentId}
          agentId={bindingAgentId}
          onClose={() => setBindingAgentId(null)}
        />
      )}

      {/* Detail modal */}
      <ToolDetailModal tool={detailTool} onClose={() => setDetailTool(null)} />

      {/* Swagger import modal */}
      <SwaggerImportModal
        visible={showSwaggerImport}
        onClose={() => setShowSwaggerImport(false)}
        onImport={handleSwaggerImport}
      />
    </section>
  );
}
