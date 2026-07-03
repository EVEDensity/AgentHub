'use client';

import { useEffect, useState, useMemo, type JSX } from 'react';
import { useAgentVersionStore } from '../../stores/agentVersionStore';
import type { AgentVersion, AgentFieldDiff } from '../../types';

// ── Props ──────────────────────────────────────────────────────────

interface Props {
  agentId: string;
  onClose?: () => void;
}

// ── Field label mapping ────────────────────────────────────────────

const FIELD_LABELS: Record<string, string> = {
  agentId: 'Agent ID',
  domain: '领域',
  adapterType: '适配器类型',
  baseModelName: '基础模型',
  rankLevel: '等级',
  displayName: '显示名称',
  dutyNote: '职责说明',
  avatarUrl: '头像 URL',
  capabilityTags: '能力标签',
  baseUrl: 'Base URL',
  apiKey: 'API Key',
  systemPrompt: 'System Prompt',
  userPrompt: 'User Prompt',
  assistantPrompt: 'Assistant Prompt',
  promptVariables: 'Prompt 变量',
};

// ── Helpers ────────────────────────────────────────────────────────

function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

function formatValue(val: unknown): string {
  if (val === undefined || val === null) return '—';
  if (Array.isArray(val)) return val.join(', ');
  if (typeof val === 'string') return val.length > 120 ? val.slice(0, 120) + '…' : val;
  return JSON.stringify(val);
}

function diffColor(type: string): string {
  switch (type) {
    case 'added':    return 'text-emerald-600 bg-emerald-50 border-emerald-200';
    case 'removed':  return 'text-red-600 bg-red-50 border-red-200';
    case 'modified': return 'text-amber-600 bg-amber-50 border-amber-200';
    default:         return 'text-warm-500 bg-warm-50 border-warm-200';
  }
}

function diffIcon(type: string): string {
  switch (type) {
    case 'added':    return 'add_circle';
    case 'removed':  return 'remove_circle';
    case 'modified': return 'edit';
    default:         return 'radio_button_unchecked';
  }
}

function diffBadge(type: string): string {
  switch (type) {
    case 'added':    return '+';
    case 'removed':  return '−';
    case 'modified': return '~';
    default:         return '=';
  }
}

// ── Sub-component: Version Timeline ────────────────────────────────

function VersionTimeline({
  versions,
  selectedId,
  diffBaseId,
  diffTargetId,
  onSelect,
  onDiffSelect,
  onRollback,
  rollingBack,
}: {
  versions: AgentVersion[];
  selectedId: string | null;
  diffBaseId: string | null;
  diffTargetId: string | null;
  onSelect: (id: string) => void;
  onDiffSelect: (id: string, role: 'base' | 'target') => void;
  onRollback: (version: number) => void;
  rollingBack: boolean;
}): JSX.Element {
  const [confirmRollback, setConfirmRollback] = useState<number | null>(null);

  return (
    <div className="space-y-1">
      {versions.map((v, i) => {
        const isSelected = selectedId === v.id;
        const isBase = diffBaseId === v.id;
        const isTarget = diffTargetId === v.id;
        const isLatest = i === 0;
        const isOldest = i === versions.length - 1;

        return (
          <div
            key={v.id}
            className={`
              relative flex items-start gap-3 px-4 py-3 rounded-xl cursor-pointer
              transition-all duration-150 border
              ${isSelected
                ? 'border-primary-300 bg-primary-50/50 shadow-sm'
                : isBase
                  ? 'border-amber-300 bg-amber-50/30'
                  : isTarget
                    ? 'border-sky-300 bg-sky-50/30'
                    : 'border-transparent hover:bg-warm-50 hover:border-warm-200'
              }
            `}
            onClick={() => onSelect(v.id)}
          >
            {/* Timeline connector */}
            <div className="flex flex-col items-center pt-1 shrink-0">
              <div
                className={`
                  w-3 h-3 rounded-full border-2 shrink-0
                  ${isLatest
                    ? 'bg-primary-500 border-primary-200 ring-2 ring-primary-100'
                    : isSelected
                      ? 'bg-primary-400 border-primary-200'
                      : 'bg-warm-200 border-warm-100'
                  }
                `}
              />
              {!isOldest && (
                <div className="w-0.5 flex-1 min-h-[12px] bg-warm-200 mt-0.5" />
              )}
            </div>

            {/* Content */}
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-sm font-semibold text-warm-800">
                  v{v.version}
                </span>
                {isLatest && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-primary-100 text-primary-700 font-medium">
                    当前
                  </span>
                )}
                <span className="text-[11px] text-warm-400">
                  {formatDate(v.createdAt)}
                </span>
                <span className="text-[11px] text-warm-400">
                  — {v.createdBy}
                </span>
              </div>

              <p className="text-xs text-warm-600 mt-0.5 line-clamp-2">
                {v.changeSummary}
              </p>

              {/* Changed field chips */}
              <div className="flex items-center gap-1 mt-1.5 flex-wrap">
                {v.changedFields.slice(0, 5).map((f) => (
                  <span
                    key={f}
                    className="text-[10px] px-1.5 py-0.5 rounded bg-warm-100 text-warm-600 font-mono"
                  >
                    {FIELD_LABELS[f] || f}
                  </span>
                ))}
                {v.changedFields.length > 5 && (
                  <span className="text-[10px] text-warm-400">
                    +{v.changedFields.length - 5} more
                  </span>
                )}
              </div>
            </div>

            {/* Actions */}
            <div className="flex items-center gap-1 shrink-0 pt-1" onClick={(e) => e.stopPropagation()}>
              {/* Diff select buttons */}
              <button
                className={`
                  text-[10px] px-2 py-1 rounded-lg font-medium transition-colors
                  ${isBase
                    ? 'bg-amber-200 text-amber-800'
                    : 'bg-warm-100 text-warm-500 hover:bg-amber-100 hover:text-amber-700'
                  }
                `}
                title="选择为对比基准 (A)"
                onClick={() => onDiffSelect(v.id, 'base')}
              >
                A
              </button>
              <button
                className={`
                  text-[10px] px-2 py-1 rounded-lg font-medium transition-colors
                  ${isTarget
                    ? 'bg-sky-200 text-sky-800'
                    : 'bg-warm-100 text-warm-500 hover:bg-sky-100 hover:text-sky-700'
                  }
                `}
                title="选择为对比目标 (B)"
                onClick={() => onDiffSelect(v.id, 'target')}
              >
                B
              </button>

              {/* Rollback */}
              {!isLatest && (
                confirmRollback === v.version ? (
                  <div className="flex items-center gap-1">
                    <button
                      className="text-[10px] px-2 py-1 rounded-lg bg-red-500 text-white font-medium hover:bg-red-600 transition-colors"
                      disabled={rollingBack}
                      onClick={() => onRollback(v.version)}
                    >
                      {rollingBack ? '…' : '确认回滚'}
                    </button>
                    <button
                      className="text-[10px] px-1.5 py-1 rounded-lg bg-warm-100 text-warm-500 hover:bg-warm-200"
                      onClick={() => setConfirmRollback(null)}
                    >
                      取消
                    </button>
                  </div>
                ) : (
                  <button
                    className="text-[10px] px-2 py-1 rounded-lg bg-warm-100 text-warm-500 hover:bg-red-50 hover:text-red-600 transition-colors"
                    title="回滚到此版本"
                    onClick={() => setConfirmRollback(v.version)}
                  >
                    <span className="material-symbols-outlined text-[12px] align-middle">history</span>
                  </button>
                )
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Sub-component: Diff Panel ──────────────────────────────────────

function DiffPanel({
  diff,
  onClose,
}: {
  diff: import('../../types').AgentVersionDiff;
  onClose: () => void;
}): JSX.Element {
  return (
    <div className="rounded-xl border border-warm-200 bg-white overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 bg-warm-50 border-b border-warm-200">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-[16px] text-warm-500">difference</span>
          <span className="text-sm font-semibold text-warm-800">
            版本对比：v{diff.versionA} → v{diff.versionB}
          </span>
        </div>
        <button
          className="p-1 rounded-lg hover:bg-warm-200 text-warm-400 transition-colors"
          onClick={onClose}
        >
          <span className="material-symbols-outlined text-[16px]">close</span>
        </button>
      </div>

      {/* Diff content */}
      <div className="divide-y divide-warm-100">
        {diff.fieldDiffs.length === 0 ? (
          <div className="px-4 py-8 text-center text-sm text-warm-400">
            两个版本之间没有差异
          </div>
        ) : (
          diff.fieldDiffs.map((fd) => (
            <FieldDiffRow key={fd.field} diff={fd} />
          ))
        )}
      </div>

      {/* Footer */}
      <div className="flex items-center gap-3 px-4 py-2.5 bg-warm-50 border-t border-warm-200 text-[10px] text-warm-400">
        <span className="flex items-center gap-1">
          <span className="inline-block w-3 h-3 rounded bg-emerald-100 text-emerald-700 text-center leading-3">+</span>
          新增
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block w-3 h-3 rounded bg-red-100 text-red-700 text-center leading-3">−</span>
          删除
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block w-3 h-3 rounded bg-amber-100 text-amber-700 text-center leading-3">~</span>
          修改
        </span>
        <span className="ml-auto">
          {diff.fieldDiffs.length} 个变更字段
        </span>
      </div>
    </div>
  );
}

// ── Sub-component: Field Diff Row ──────────────────────────────────

function FieldDiffRow({ diff: fd }: { diff: AgentFieldDiff }): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const isLong =
    (typeof fd.oldValue === 'string' && fd.oldValue.length > 80) ||
    (typeof fd.newValue === 'string' && fd.newValue.length > 80);

  return (
    <div className={`px-4 py-3 ${diffColor(fd.type)}`}>
      <div
        className="flex items-start gap-3 cursor-pointer"
        onClick={() => isLong && setExpanded(!expanded)}
      >
        {/* Type badge */}
        <span className={`
          shrink-0 w-6 h-6 rounded-lg flex items-center justify-center text-[11px] font-bold
          ${fd.type === 'added' ? 'bg-emerald-200 text-emerald-800' : ''}
          ${fd.type === 'removed' ? 'bg-red-200 text-red-800' : ''}
          ${fd.type === 'modified' ? 'bg-amber-200 text-amber-800' : ''}
        `}>
          {diffBadge(fd.type)}
        </span>

        {/* Field name */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold">{fd.label}</span>
            <code className="text-[10px] text-warm-400 font-mono">{fd.field}</code>
            {isLong && (
              <span className="text-[10px] text-primary-500 ml-auto">
                {expanded ? '收起' : '展开'}
              </span>
            )}
          </div>

          {/* Values */}
          <div className="mt-1 space-y-1">
            {fd.type !== 'added' && (
              <div className="flex items-start gap-2">
                <span className="text-[10px] text-red-500 font-medium shrink-0 mt-0.5">旧值:</span>
                <span className={`text-xs font-mono text-red-700 ${!expanded && isLong ? 'line-clamp-1' : 'whitespace-pre-wrap'}`}>
                  {formatValue(fd.oldValue)}
                </span>
              </div>
            )}
            {fd.type !== 'removed' && (
              <div className="flex items-start gap-2">
                <span className="text-[10px] text-emerald-500 font-medium shrink-0 mt-0.5">新值:</span>
                <span className={`text-xs font-mono text-emerald-700 ${!expanded && isLong ? 'line-clamp-1' : 'whitespace-pre-wrap'}`}>
                  {formatValue(fd.newValue)}
                </span>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Sub-component: Version Detail Panel ────────────────────────────

function VersionDetail({
  version,
  onClose,
  onRollback,
  rollingBack,
  isLatest,
}: {
  version: AgentVersion;
  onClose: () => void;
  onRollback: (v: number) => void;
  rollingBack: boolean;
  isLatest: boolean;
}): JSX.Element {
  const [confirming, setConfirming] = useState(false);
  const entries = Object.entries(version.snapshot).filter(
    ([k]) => k !== 'apiKey' // Never show API keys
  );

  return (
    <div className="rounded-xl border border-warm-200 bg-white overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 bg-warm-50 border-b border-warm-200">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-[16px] text-warm-500">description</span>
          <span className="text-sm font-semibold text-warm-800">
            版本 v{version.version} 快照
          </span>
          {isLatest && (
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-primary-100 text-primary-700 font-medium">
              当前版本
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {!isLatest && (
            confirming ? (
              <div className="flex items-center gap-1">
                <button
                  className="text-[10px] px-2 py-1 rounded-lg bg-red-500 text-white font-medium hover:bg-red-600 transition-colors"
                  disabled={rollingBack}
                  onClick={() => onRollback(version.version)}
                >
                  {rollingBack ? '回滚中…' : '确认回滚'}
                </button>
                <button
                  className="text-[10px] px-1.5 py-1 rounded-lg bg-warm-100 text-warm-500 hover:bg-warm-200"
                  onClick={() => setConfirming(false)}
                >
                  取消
                </button>
              </div>
            ) : (
              <button
                className="text-[11px] px-2.5 py-1 rounded-lg bg-warm-100 text-warm-600 hover:bg-red-50 hover:text-red-600 transition-colors flex items-center gap-1"
                onClick={() => setConfirming(true)}
              >
                <span className="material-symbols-outlined text-[12px]">history</span>
                回滚
              </button>
            )
          )}
          <button
            className="p-1 rounded-lg hover:bg-warm-200 text-warm-400 transition-colors"
            onClick={onClose}
          >
            <span className="material-symbols-outlined text-[16px]">close</span>
          </button>
        </div>
      </div>

      {/* Snapshot fields */}
      <div className="divide-y divide-warm-100 max-h-[400px] overflow-y-auto">
        {entries.map(([key, val]) => (
          <div key={key} className="px-4 py-2.5 flex items-start gap-3">
            <span className="text-[11px] font-medium text-warm-600 shrink-0 w-28">
              {FIELD_LABELS[key] || key}
            </span>
            <span className="text-xs text-warm-800 font-mono break-all whitespace-pre-wrap">
              {formatValue(val)}
            </span>
          </div>
        ))}
      </div>

      {/* Metadata */}
      <div className="px-4 py-2.5 bg-warm-50 border-t border-warm-200 flex items-center gap-4 text-[10px] text-warm-400">
        <span>创建者: {version.createdBy}</span>
        <span>时间: {formatDate(version.createdAt)}</span>
        <span>变更: {version.changeSummary}</span>
      </div>
    </div>
  );
}

// ── Main Component ─────────────────────────────────────────────────

export default function AgentVersionHistory({ agentId, onClose }: Props): JSX.Element {
  const {
    versions,
    selectedVersionId,
    diffBaseVersionId,
    diffTargetVersionId,
    currentDiff,
    loading,
    diffLoading,
    rollingBack,
    loadVersions,
    compareVersions,
    rollback,
    clearDiff,
    setSelectedVersion,
    setDiffBase,
    setDiffTarget,
  } = useAgentVersionStore();

  const [mode, setMode] = useState<'timeline' | 'detail' | 'diff'>('timeline');

  useEffect(() => {
    if (agentId) {
      loadVersions(agentId);
    }
  }, [agentId, loadVersions]);

  // Derived state
  const selectedVersion = useMemo(
    () => versions.find((v) => v.id === selectedVersionId) || null,
    [versions, selectedVersionId]
  );

  const canCompare = diffBaseVersionId && diffTargetVersionId && diffBaseVersionId !== diffTargetVersionId;

  const handleSelect = (id: string) => {
    setSelectedVersion(id);
    setMode('detail');
  };

  const handleDiffSelect = (id: string, role: 'base' | 'target') => {
    if (role === 'base') {
      setDiffBase(id);
    } else {
      setDiffTarget(id);
    }
  };

  const handleCompare = () => {
    const baseV = versions.find((v) => v.id === diffBaseVersionId);
    const targetV = versions.find((v) => v.id === diffTargetVersionId);
    if (baseV && targetV) {
      compareVersions(agentId, baseV.version, targetV.version);
      setMode('diff');
    }
  };

  const handleRollback = async (version: number) => {
    const ok = await rollback(agentId, version);
    if (ok) {
      await loadVersions(agentId);
      setMode('timeline');
      setSelectedVersion(null);
    }
  };

  const isLoading = loading || diffLoading;

  return (
    <div className="bg-white rounded-2xl border border-warm-200 shadow-sm overflow-hidden">
      {/* ── Header ─────────────────────────────────────────────── */}
      <div className="flex items-center justify-between px-5 py-3.5 border-b border-warm-200 bg-gradient-to-r from-warm-50 to-white">
        <div className="flex items-center gap-2.5">
          <span className="material-symbols-outlined text-[18px] text-primary-500">history</span>
          <div>
            <h3 className="text-sm font-bold text-warm-800">版本历史</h3>
            <p className="text-[10px] text-warm-400 font-mono">{agentId}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {/* Compare button */}
          {canCompare && (
            <button
              className="text-[11px] px-3 py-1.5 rounded-lg bg-primary-500 text-white font-medium hover:bg-primary-600 transition-colors flex items-center gap-1"
              onClick={handleCompare}
            >
              <span className="material-symbols-outlined text-[14px]">difference</span>
              对比版本
            </button>
          )}
          {/* Mode toggle */}
          <div className="flex rounded-lg bg-warm-100 p-0.5">
            <button
              className={`text-[11px] px-2.5 py-1 rounded-md font-medium transition-colors ${mode === 'timeline' ? 'bg-white text-warm-800 shadow-sm' : 'text-warm-500'}`}
              onClick={() => { setMode('timeline'); setSelectedVersion(null); clearDiff(); }}
            >
              时间线
            </button>
            {currentDiff && (
              <button
                className={`text-[11px] px-2.5 py-1 rounded-md font-medium transition-colors ${mode === 'diff' ? 'bg-white text-warm-800 shadow-sm' : 'text-warm-500'}`}
                onClick={() => setMode('diff')}
              >
                对比结果
              </button>
            )}
          </div>
          {onClose && (
            <button
              className="p-1.5 rounded-lg hover:bg-warm-200 text-warm-400 transition-colors"
              onClick={onClose}
            >
              <span className="material-symbols-outlined text-[16px]">close</span>
            </button>
          )}
        </div>
      </div>

      {/* ── Diff selection hint ────────────────────────────────── */}
      {versions.length >= 2 && mode === 'timeline' && (
        <div className="px-5 py-2 bg-amber-50/50 border-b border-amber-100 flex items-center gap-2 text-[10px] text-amber-700">
          <span className="material-symbols-outlined text-[12px]">info</span>
          点击版本行上的 <strong>A</strong> 和 <strong>B</strong> 按钮选择两个版本进行对比
          {(diffBaseVersionId || diffTargetVersionId) && (
            <button
              className="ml-auto text-warm-400 hover:text-warm-600"
              onClick={() => { setDiffBase(null); setDiffTarget(null); }}
            >
              清除选择
            </button>
          )}
        </div>
      )}

      {/* ── Body ──────────────────────────────────────────────── */}
      <div className="p-4">
        {loading && versions.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 gap-3">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary-200 border-t-primary-500" />
            <span className="text-xs text-warm-400">加载版本历史...</span>
          </div>
        ) : versions.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 gap-2 text-warm-400">
            <span className="material-symbols-outlined text-[32px]">history</span>
            <span className="text-xs">暂无版本记录</span>
            <span className="text-[10px]">保存 Agent 配置后将自动创建版本快照</span>
          </div>
        ) : mode === 'diff' && currentDiff ? (
          <DiffPanel diff={currentDiff} onClose={() => { clearDiff(); setMode('timeline'); }} />
        ) : mode === 'detail' && selectedVersion ? (
          <VersionDetail
            version={selectedVersion}
            onClose={() => { setSelectedVersion(null); setMode('timeline'); }}
            onRollback={handleRollback}
            rollingBack={rollingBack}
            isLatest={selectedVersion.version === versions[0]?.version}
          />
        ) : (
          <VersionTimeline
            versions={versions}
            selectedId={selectedVersionId}
            diffBaseId={diffBaseVersionId}
            diffTargetId={diffTargetVersionId}
            onSelect={handleSelect}
            onDiffSelect={handleDiffSelect}
            onRollback={handleRollback}
            rollingBack={rollingBack}
          />
        )}
      </div>

      {/* ── Footer ─────────────────────────────────────────────── */}
      <div className="px-5 py-2.5 border-t border-warm-200 bg-warm-50 flex items-center justify-between text-[10px] text-warm-400">
        <span>共 {versions.length} 个版本</span>
        <span>{versions.length > 0 ? `最新版本: v${versions[0]?.version} (${formatDate(versions[0]?.createdAt || '')})` : ''}</span>
      </div>
    </div>
  );
}
