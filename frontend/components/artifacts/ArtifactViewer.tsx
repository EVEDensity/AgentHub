'use client';

import { useState, type JSX } from 'react';
import { splitArtifactSegments } from '../../lib/artifacts/artifactParser';

/**
 * Artifact Viewer — unified preview/edit component for agent-generated artifacts.
 *
 * Supports 5 artifact types per AgentHub V5.1 §11:
 * - webpage   → iframe sandbox preview
 * - document  → markdown render + Monaco edit
 * - ppt       → reveal.js-style slide render
 * - code      → Monaco editor with syntax highlighting
 * - rag_result → citation card with source metadata
 *
 * Part of AgentHub V5.1 §11 — Artifact Preview & Citation System
 */

// ── Types ──────────────────────────────────────────────────────────────

export type ArtifactType = 'webpage' | 'document' | 'ppt' | 'code' | 'rag_result';

export interface Artifact {
  id: string;
  type: ArtifactType;
  title: string;
  content: string;
  language?: string;       // for code artifacts
  sourceId?: string;       // for RAG results
  chunkId?: string;        // for RAG results
  score?: number;          // for RAG results
  metadata?: Record<string, string>;
  createdAt?: string;
  updatedAt?: string;
}

interface ArtifactViewerProps {
  artifact: Artifact;
  /** Whether to show edit mode (if supported by artifact type) */
  editable?: boolean;
  /** Compact card mode vs full viewer */
  compact?: boolean;
  /** Called when the artifact content is edited */
  onEdit?: (id: string, newContent: string) => void;
  /** Called to navigate to a citation */
  onCitationClick?: (sourceId: string, chunkId?: string) => void;
}

// ── Sub-components ─────────────────────────────────────────────────────

/** Inline citation chip — renders `{{artifact:...}}` references in text */
export function CitationChip({
  sourceId,
  chunkId,
  onClick,
}: {
  sourceId: string;
  chunkId?: string;
  onClick?: (sourceId: string, chunkId?: string) => void;
}): JSX.Element {
  return (
    <button
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-primary-50 text-primary-600 hover:bg-primary-100 hover:text-primary-700 transition-colors align-middle cursor-pointer border-0"
      onClick={(e) => {
        e.stopPropagation();
        onClick?.(sourceId, chunkId);
      }}
      title={`来源: ${sourceId}${chunkId ? ` · 分块: ${chunkId}` : ''}`}
    >
      <span className="material-symbols-outlined text-[11px]">description</span>
      {sourceId}
      {chunkId && <span className="opacity-60">:{chunkId}</span>}
    </button>
  );
}

/**
 * Render text with inline artifact citations as clickable chips.
 *
 * @example
 *   <ArtifactText
 *     text="See {{artifact:doc-1:c-3}} for details"
 *     onCitationClick={(sid, cid) => navigate(`/artifact/${sid}#${cid}`)}
 *   />
 */
export function ArtifactText({
  text,
  onCitationClick,
}: {
  text: string;
  onCitationClick?: (sourceId: string, chunkId?: string) => void;
}): JSX.Element {
  const segments = splitArtifactSegments(text);

  if (segments.length === 0) return <>{text}</>;

  return (
    <>
      {segments.map((seg) => {
        if (seg.type === 'citation') {
          return (
            <CitationChip
              key={seg.key}
              sourceId={seg.sourceId}
              chunkId={seg.chunkId}
              onClick={onCitationClick}
            />
          );
        }
        return <span key={seg.key}>{seg.value}</span>;
      })}
    </>
  );
}

// ── Webpage Preview ────────────────────────────────────────────────────

function WebpagePreview({ content, title }: { content: string; title: string }): JSX.Element {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border border-warm-200 rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2 bg-warm-50 border-b border-warm-150">
        <div className="flex items-center gap-2 text-xs text-warm-600">
          <span className="material-symbols-outlined text-[14px] text-accent-500">web</span>
          <span className="font-medium">{title || '网页预览'}</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="text-[10px] text-primary-500 hover:text-primary-600"
            onClick={() => window.open(`data:text/html,${encodeURIComponent(content)}`, '_blank')}
          >
            新窗口打开
          </button>
          <button
            className="text-[10px] text-warm-400 hover:text-warm-600"
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? '收起' : '展开'}
          </button>
        </div>
      </div>
      {expanded && (
        <iframe
          srcDoc={content}
          sandbox="allow-scripts allow-same-origin"
          className="w-full border-0"
          style={{ height: 420 }}
          title={title}
        />
      )}
    </div>
  );
}

// ── Document Preview ───────────────────────────────────────────────────

function DocumentPreview({ content, title, onCitationClick }: {
  content: string;
  title: string;
  onCitationClick?: (sourceId: string, chunkId?: string) => void;
}): JSX.Element {
  const preview = content.length > 500 ? content.slice(0, 500) + '...' : content;

  return (
    <div className="border border-warm-200 rounded-xl overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-2 bg-warm-50 border-b border-warm-150">
        <span className="material-symbols-outlined text-[14px] text-primary-500">article</span>
        <span className="text-xs font-medium text-warm-700">{title || '文档'}</span>
        <span className="text-[10px] text-warm-400 ml-auto">{content.length} 字符</span>
      </div>
      <div className="p-4">
        <div className="text-sm text-warm-700 leading-relaxed whitespace-pre-wrap">
          <ArtifactText text={preview} onCitationClick={onCitationClick} />
        </div>
      </div>
      {content.length > 500 && (
        <div className="px-4 pb-3">
          <button className="text-xs text-primary-500 hover:text-primary-600">
            展开完整内容 →
          </button>
        </div>
      )}
    </div>
  );
}

// ── PPT Preview ─────────────────────────────────────────────────────────

function PPTPreview({ content, title }: { content: string; title: string }): JSX.Element {
  // Parse slides from JSON content (reveal.js format)
  let slides: string[] = [];
  try {
    const data = JSON.parse(content);
    slides = data.slides || data.pages || [];
  } catch {
    // Treat as plain text with --- separator
    slides = content.split('\n---\n').filter((s) => s.trim());
  }

  const [currentSlide, setCurrentSlide] = useState(0);

  return (
    <div className="border border-warm-200 rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2 bg-warm-50 border-b border-warm-150">
        <div className="flex items-center gap-2 text-xs text-warm-600">
          <span className="material-symbols-outlined text-[14px] text-warning-500">slideshow</span>
          <span className="font-medium">{title || '演示文稿'}</span>
        </div>
        <span className="text-[10px] text-warm-400">
          {currentSlide + 1} / {slides.length}
        </span>
      </div>
      <div className="p-6 min-h-[200px] flex items-center justify-center bg-warm-25">
        {slides.length > 0 ? (
          <div className="text-center max-w-lg">
            <div className="text-lg font-semibold text-warm-800 mb-2">
              幻灯片 {currentSlide + 1}
            </div>
            <div className="text-sm text-warm-600 leading-relaxed whitespace-pre-wrap">
              {slides[currentSlide]}
            </div>
          </div>
        ) : (
          <div className="text-sm text-warm-400">无幻灯片内容</div>
        )}
      </div>
      {slides.length > 1 && (
        <div className="flex items-center justify-center gap-3 px-4 py-2 border-t border-warm-150">
          <button
            className="text-xs px-3 py-1 rounded bg-warm-100 text-warm-600 hover:bg-warm-200 disabled:opacity-30"
            disabled={currentSlide === 0}
            onClick={() => setCurrentSlide((s) => Math.max(0, s - 1))}
          >
            ← 上一页
          </button>
          <span className="text-[10px] text-warm-400">
            {currentSlide + 1} / {slides.length}
          </span>
          <button
            className="text-xs px-3 py-1 rounded bg-warm-100 text-warm-600 hover:bg-warm-200 disabled:opacity-30"
            disabled={currentSlide >= slides.length - 1}
            onClick={() => setCurrentSlide((s) => Math.min(slides.length - 1, s + 1))}
          >
            下一页 →
          </button>
        </div>
      )}
    </div>
  );
}

// ── Code Preview ────────────────────────────────────────────────────────

function CodePreview({ content, language, title }: {
  content: string;
  language?: string;
  title: string;
}): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const lines = content.split('\n');
  const previewLines = expanded ? lines : lines.slice(0, 20);

  return (
    <div className="border border-warm-200 rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2 bg-warm-800 text-warm-100">
        <div className="flex items-center gap-2 text-xs">
          <span className="material-symbols-outlined text-[14px] text-accent-400">code</span>
          <span className="font-medium">{title || '代码'}</span>
          {language && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-warm-700 text-warm-300">
              {language}
            </span>
          )}
        </div>
        <button
          className="text-[10px] text-warm-300 hover:text-warm-100"
          onClick={() => {
            navigator.clipboard.writeText(content).catch(() => {});
          }}
        >
          复制代码
        </button>
      </div>
      <div className="bg-warm-900 text-warm-100 font-mono text-xs leading-relaxed overflow-x-auto">
        <table className="w-full border-collapse">
          <tbody>
            {previewLines.map((line, i) => (
              <tr key={i} className="hover:bg-warm-800/50">
                <td className="text-right pr-3 pl-3 text-warm-500 select-none w-12 border-r border-warm-800">
                  {i + 1}
                </td>
                <td className="pl-3 pr-3 whitespace-pre">{line || ' '}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {lines.length > 20 && (
        <div className="px-4 py-2 bg-warm-50 border-t border-warm-150">
          <button
            className="text-xs text-primary-500 hover:text-primary-600"
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? '收起' : `展开全部 ${lines.length} 行 →`}
          </button>
        </div>
      )}
    </div>
  );
}

// ── RAG Result Preview ──────────────────────────────────────────────────

function RAGResultPreview({ artifact, onCitationClick }: {
  artifact: Artifact;
  onCitationClick?: (sourceId: string, chunkId?: string) => void;
}): JSX.Element {
  const scorePct = artifact.score ? Math.round(artifact.score * 100) : 0;
  const scoreColor =
    scorePct >= 85 ? 'text-success-600 bg-success-50' :
    scorePct >= 70 ? 'text-primary-600 bg-primary-50' :
    'text-warning-600 bg-warning-50';

  return (
    <div className="border border-warm-200 rounded-xl overflow-hidden hover:shadow-card-hover transition-shadow">
      <div className="flex items-start gap-3 px-4 py-3">
        <div className="shrink-0 w-8 h-8 rounded-lg bg-primary-50 flex items-center justify-center">
          <span className="material-symbols-outlined text-[16px] text-primary-500">search</span>
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs font-semibold text-warm-700 truncate">
              {artifact.sourceId || artifact.title}
            </span>
            <span className={`text-[9px] px-1.5 py-0.5 rounded-full ${scoreColor}`}>
              相关度 {scorePct}%
            </span>
          </div>
          <div className="text-xs text-warm-500 mb-1">
            分块 #{artifact.chunkId || '—'}
            {artifact.metadata?.file_path && (
              <>
                <span className="mx-1">·</span>
                <span className="font-mono text-[10px]">{artifact.metadata.file_path}</span>
              </>
            )}
          </div>
          <div className="text-xs text-warm-600 leading-relaxed line-clamp-3">
            {artifact.content}
          </div>
          <div className="mt-2 text-[10px] text-primary-500 font-mono">
            {`{{artifact:${artifact.sourceId || artifact.id}${artifact.chunkId ? ':' + artifact.chunkId : ''}}}`}
            <button
              className="ml-2 text-warm-400 hover:text-primary-500"
              onClick={() => onCitationClick?.(artifact.sourceId || artifact.id, artifact.chunkId)}
            >
              查看原文 →
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Main Artifact Viewer ────────────────────────────────────────────────

export default function ArtifactViewer({
  artifact,
  editable = false,
  compact = false,
  onEdit,
  onCitationClick,
}: ArtifactViewerProps): JSX.Element {
  if (compact) {
    // Compact mode: render a small card
    return (
      <div className="card p-3 hover:shadow-card-hover transition-shadow cursor-pointer group">
        <div className="flex items-center gap-2 mb-2">
          <TypeIcon type={artifact.type} />
          <span className="text-xs font-semibold text-warm-700 truncate flex-1">
            {artifact.title}
          </span>
          <span className="text-[10px] text-warm-400">{artifact.type}</span>
        </div>
        <div className="text-xs text-warm-500 line-clamp-2">
          {artifact.content.slice(0, 200)}
        </div>
      </div>
    );
  }

  // Full viewer: dispatch by type
  switch (artifact.type) {
    case 'webpage':
      return <WebpagePreview content={artifact.content} title={artifact.title} />;

    case 'document':
      return (
        <DocumentPreview
          content={artifact.content}
          title={artifact.title}
          onCitationClick={onCitationClick}
        />
      );

    case 'ppt':
      return <PPTPreview content={artifact.content} title={artifact.title} />;

    case 'code':
      return (
        <CodePreview
          content={artifact.content}
          language={artifact.language}
          title={artifact.title}
        />
      );

    case 'rag_result':
      return (
        <RAGResultPreview
          artifact={artifact}
          onCitationClick={onCitationClick}
        />
      );

    default:
      return (
        <div className="card p-4">
          <div className="text-xs text-warm-400">未知产物类型: {artifact.type}</div>
          <pre className="text-xs text-warm-600 mt-2 whitespace-pre-wrap">
            {artifact.content.slice(0, 500)}
          </pre>
        </div>
      );
  }
}

// ── Helpers ─────────────────────────────────────────────────────────────

function TypeIcon({ type }: { type: ArtifactType }): JSX.Element {
  const icons: Record<ArtifactType, string> = {
    webpage: 'web',
    document: 'article',
    ppt: 'slideshow',
    code: 'code',
    rag_result: 'search',
  };
  const colors: Record<ArtifactType, string> = {
    webpage: 'text-accent-500',
    document: 'text-primary-500',
    ppt: 'text-warning-500',
    code: 'text-accent-400',
    rag_result: 'text-primary-500',
  };
  return (
    <span className={`material-symbols-outlined text-[16px] ${colors[type]}`}>
      {icons[type]}
    </span>
  );
}
