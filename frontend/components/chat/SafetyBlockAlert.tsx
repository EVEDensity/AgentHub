import { memo } from 'react';
import type { GuardrailResult } from '../../types';

interface SafetyBlockAlertProps {
  result: GuardrailResult;
}

const CATEGORY_ICONS: Record<string, string> = {
  pii: '[lock]',
  injection: '[shield]',
  harmful: '[warn]',
  high_risk_op: '[secure]',
};

const CATEGORY_LABELS: Record<string, string> = {
  pii: '隐私信息泄露',
  injection: '注入攻击检测',
  harmful: '有害内容',
  high_risk_op: '高风险操作',
};

const SafetyBlockAlert = memo(function SafetyBlockAlert({ result }: SafetyBlockAlertProps) {
  if (!result.blocked && result.flags.length === 0) return null;

  return (
    <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-base">[block]</span>
        <span className="text-sm font-semibold text-red-800">
          {result.blocked ? '安全护栏已阻断' : '安全护栏检测'}
        </span>
        {result.blocked && (
          <span className="rounded-full bg-red-200 px-2 py-0.5 text-xs font-medium text-red-700">
            已阻断
          </span>
        )}
      </div>
      <ul className="space-y-1.5">
        {result.flags.map((flag, i) => (
          <li key={`${flag.rule}-${i}`} className="flex items-start gap-2 text-xs text-red-700">
            <span className="mt-0.5 shrink-0">
              {CATEGORY_ICONS[flag.category] || '•'}
            </span>
            <div>
              <span className="font-medium">
                {CATEGORY_LABELS[flag.category] || flag.category}
              </span>
              <span className="mx-1 text-red-400">—</span>
              <span>{flag.message}</span>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
});

export default SafetyBlockAlert;
