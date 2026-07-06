import { memo, useState, useEffect, useCallback, useRef } from 'react';
import type { SolutionProposalEvent, SolutionOption } from '../../types';

interface SolutionBubbleProps {
  data: SolutionProposalEvent;
  onSelectSolution: (solutionId: string, autoSelected: boolean) => void;
  resolvedBy?: string;
  resolvedByName?: string;
}

const RISK_COLORS: Record<string, { bg: string; text: string; label: string }> = {
  low: { bg: '#F2F8F2', text: '#5B8C5A', label: '低风险' },
  medium: { bg: '#FFF8ED', text: '#D98B2B', label: '中风险' },
  high: { bg: '#FBF0EE', text: '#C4675A', label: '高风险' },
};

function formatCountdown(seconds: number): string {
  if (seconds <= 0) return '正在确认...';
  return `剩余 ${seconds} 秒`;
}

const SolutionBubble = memo(function SolutionBubble({
  data,
  onSelectSolution,
  resolvedBy,
  resolvedByName,
}: SolutionBubbleProps) {
  const {
    solutions,
    recommendedSolutionId,
    recommendationReason,
    requirements,
    nonFunctionalRequirements,
    constraints,
    autoConfirmSeconds,
    messageId,
    sessionId,
  } = data;

  const [countdown, setCountdown] = useState(autoConfirmSeconds || 15);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showRequirements, setShowRequirements] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const hasSelected = useRef(false);

  const isResolved = !!resolvedBy;

  // ── Auto-countdown ────────────────────────────────────────────────
  useEffect(() => {
    if (isResolved || selectedId) return;

    timerRef.current = setInterval(() => {
      setCountdown((prev) => {
        if (prev <= 1) {
          if (timerRef.current) clearInterval(timerRef.current);
          // Auto-select recommended solution
          if (!hasSelected.current) {
            hasSelected.current = true;
            // Defer to next tick to avoid setState-during-render
            setTimeout(() => onSelectSolution(recommendedSolutionId, true), 0);
          }
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [isResolved, selectedId, recommendedSolutionId, onSelectSolution]);

  const handleSelect = useCallback(
    (solutionId: string) => {
      if (isResolved || hasSelected.current) return;
      hasSelected.current = true;
      setSelectedId(solutionId);
      if (timerRef.current) clearInterval(timerRef.current);
      onSelectSolution(solutionId, false);
    },
    [isResolved, onSelectSolution],
  );

  // ── Countdown bar ─────────────────────────────────────────────────
  const pct = autoConfirmSeconds > 0
    ? Math.round((countdown / autoConfirmSeconds) * 100)
    : 0;

  return (
    <div className="my-3 mx-auto max-w-[720px] rounded-2xl border border-warm-150 bg-warm-100 shadow-sm overflow-hidden">
      {/* Header */}
      <div className="border-b border-warm-100 bg-gradient-to-r from-primary-50/50 to-white px-5 py-3.5">
        <div className="flex items-center gap-2.5">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary-100 text-xs font-bold text-primary-600">
            [idea]
          </span>
          <div className="flex-1 min-w-0">
            <h4 className="text-sm font-semibold text-warm-800">
              方案分析
            </h4>
            <p className="mt-0.5 text-[11px] text-warm-400 truncate">
              已为您分析 {solutions.length} 个技术方案
            </p>
          </div>
          {/* Countdown badge */}
          {!isResolved && !selectedId && countdown > 0 && (
            <span className="flex-shrink-0 rounded-full bg-primary-50 px-2.5 py-1 text-[11px] font-medium text-primary-600">
              [time] {formatCountdown(countdown)}
            </span>
          )}
          {isResolved && (
            <span className="flex-shrink-0 rounded-full bg-warm-100 px-2.5 py-1 text-[11px] font-medium text-warm-500">
              ✓ 已确认 by {resolvedByName}
            </span>
          )}
        </div>
        {/* Countdown progress bar */}
        {!isResolved && !selectedId && (
          <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-warm-100">
            <div
              className="h-full rounded-full transition-all duration-1000 ease-linear"
              style={{
                width: `${pct}%`,
                background: pct > 30
                  ? 'linear-gradient(90deg, #4F6CF7, #8099FB)'
                  : 'linear-gradient(90deg, #D98B2B, #F0A04B)',
              }}
            />
          </div>
        )}
      </div>

      {/* Requirements (collapsible) */}
      {(requirements.length > 0 || nonFunctionalRequirements.length > 0 || constraints.length > 0) && (
        <div className="border-b border-warm-100 px-5 py-3">
          <button
            onClick={() => setShowRequirements(!showRequirements)}
            className="flex w-full items-center gap-2 text-[12px] font-medium text-warm-500 hover:text-warm-700 transition-colors"
          >
            <svg
              className={`h-3 w-3 transition-transform ${showRequirements ? 'rotate-90' : ''}`}
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
            >
              <polyline points="9 18 15 12 9 6" />
            </svg>
            需求详情
            <span className="text-warm-400 font-normal">
              ({requirements.length}功能需求{nonFunctionalRequirements.length > 0 ? ` / ${nonFunctionalRequirements.length}非功能需求` : ''})
            </span>
          </button>
          {showRequirements && (
            <div className="mt-2.5 grid grid-cols-1 sm:grid-cols-2 gap-2">
              {requirements.length > 0 && (
                <div>
                  <span className="text-[10px] font-semibold uppercase tracking-wide text-warm-400">功能需求</span>
                  <ul className="mt-1 space-y-0.5">
                    {requirements.map((r, i) => (
                      <li key={i} className="flex items-start gap-1.5 text-[11px] text-warm-600">
                        <span className="mt-0.5 h-1 w-1 flex-shrink-0 rounded-full bg-primary-400" />
                        {r}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {(nonFunctionalRequirements.length > 0 || constraints.length > 0) && (
                <div>
                  {nonFunctionalRequirements.length > 0 && (
                    <>
                      <span className="text-[10px] font-semibold uppercase tracking-wide text-warm-400">非功能需求</span>
                      <ul className="mt-1 space-y-0.5">
                        {nonFunctionalRequirements.map((r, i) => (
                          <li key={i} className="flex items-start gap-1.5 text-[11px] text-warm-600">
                            <span className="mt-0.5 h-1 w-1 flex-shrink-0 rounded-full bg-accent-400" />
                            {r}
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                  {constraints.length > 0 && (
                    <div className="mt-2">
                      <span className="text-[10px] font-semibold uppercase tracking-wide text-warm-400">技术约束</span>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {constraints.map((c, i) => (
                          <span key={i} className="rounded-full bg-warm-100 px-2 py-0.5 text-[10px] text-warm-600">
                            {c}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Solution cards */}
      <div className="px-5 py-4">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {solutions.map((sol) => {
            const isRecommended = sol.id === recommendedSolutionId;
            const isSelected = sol.id === selectedId;
            const risk = RISK_COLORS[sol.riskLevel] || RISK_COLORS.medium;

            return (
              <SolutionCard
                key={sol.id}
                solution={sol}
                isRecommended={isRecommended}
                isSelected={isSelected}
                isDisabled={isResolved || hasSelected.current}
                risk={risk}
                onSelect={handleSelect}
              />
            );
          })}
        </div>
      </div>

      {/* Recommendation reason */}
      {recommendationReason && (
        <div className="border-t border-warm-100 bg-primary-50/30 px-5 py-3">
          <div className="flex items-start gap-2">
            <span className="mt-0.5 text-xs text-primary-500 font-bold">*</span>
            <div>
              <span className="text-[11px] font-semibold text-warm-700">推荐理由</span>
              <p className="mt-0.5 text-[11px] text-warm-500 leading-relaxed">{recommendationReason}</p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
});

// ── Sub-component: individual solution card ───────────────────────────

interface SolutionCardProps {
  solution: SolutionOption;
  isRecommended: boolean;
  isSelected: boolean;
  isDisabled: boolean;
  risk: { bg: string; text: string; label: string };
  onSelect: (id: string) => void;
}

const SolutionCard = memo(function SolutionCard({
  solution,
  isRecommended,
  isSelected,
  isDisabled,
  risk,
  onSelect,
}: SolutionCardProps) {
  return (
    <div
      className={`relative flex flex-col rounded-xl border-2 p-4 transition-all ${
        isRecommended
          ? 'border-primary-300 bg-primary-50/20 shadow-sm shadow-primary-100/50'
          : isSelected
          ? 'border-accent-300 bg-accent-50/20'
          : 'border-warm-150 bg-warm-100 hover:border-warm-200'
      } ${isDisabled ? 'opacity-60 pointer-events-none' : ''}`}
    >
      {/* Recommended badge */}
      {isRecommended && (
        <div className="absolute -top-2.5 right-3 rounded-full bg-primary-500 px-2.5 py-0.5 text-[10px] font-semibold text-white shadow-sm">
          * 推荐方案
        </div>
      )}

      {/* Solution name */}
      <h5 className="text-sm font-semibold text-warm-800 mb-2 pr-16">{solution.name}</h5>

      {/* Score */}
      <div className="absolute top-3 right-3 flex h-9 w-9 items-center justify-center rounded-full bg-warm-50 border border-warm-150">
        <span className="text-xs font-bold text-warm-700">{solution.score}</span>
      </div>

      {/* Tech stack tags */}
      <div className="flex flex-wrap gap-1 mb-3">
        {solution.techStack.slice(0, 5).map((tech, i) => (
          <span
            key={i}
            className="rounded-md bg-warm-100 px-1.5 py-0.5 text-[10px] font-medium text-warm-600"
          >
            {tech}
          </span>
        ))}
        {solution.techStack.length > 5 && (
          <span className="text-[10px] text-warm-400">+{solution.techStack.length - 5}</span>
        )}
      </div>

      {/* Architecture */}
      <p className="text-[11px] text-warm-500 leading-relaxed mb-3 flex-1">
        {solution.architecture}
      </p>

      {/* Pros */}
      <div className="mb-2">
        <span className="text-[10px] font-semibold text-green-600 uppercase tracking-wide">优点</span>
        <ul className="mt-1 space-y-0.5">
          {solution.pros.slice(0, 3).map((p, i) => (
            <li key={i} className="flex items-start gap-1 text-[10px] text-warm-600">
              <span className="mt-0.5 text-green-400 flex-shrink-0">+</span>
              {p}
            </li>
          ))}
        </ul>
      </div>

      {/* Cons */}
      {solution.cons.length > 0 && (
        <div className="mb-3">
          <span className="text-[10px] font-semibold text-red-500 uppercase tracking-wide">缺点</span>
          <ul className="mt-1 space-y-0.5">
            {solution.cons.slice(0, 2).map((c, i) => (
              <li key={i} className="flex items-start gap-1 text-[10px] text-warm-500">
                <span className="mt-0.5 text-red-300 flex-shrink-0">−</span>
                {c}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Metadata row */}
      <div className="flex items-center gap-2 mb-3 text-[10px] text-warm-400">
        <span>[time] {solution.estimatedEffort}</span>
        <span className="text-warm-200">|</span>
        <span
          className="rounded-full px-1.5 py-0.5 font-medium"
          style={{ background: risk.bg, color: risk.text }}
        >
          {risk.label}
        </span>
      </div>

      {/* Select button */}
      <button
        onClick={() => onSelect(solution.id)}
        disabled={isDisabled}
        className={`w-full rounded-lg px-3 py-2 text-[12px] font-semibold transition-all ${
          isRecommended
            ? 'bg-primary-500 text-white hover:bg-primary-600 active:scale-[0.98]'
            : 'bg-warm-100 text-warm-700 hover:bg-warm-200 active:scale-[0.98]'
        } disabled:opacity-50 disabled:cursor-not-allowed`}
      >
        {isSelected ? '✓ 已选择' : '选择此方案'}
      </button>
    </div>
  );
});

export default SolutionBubble;
