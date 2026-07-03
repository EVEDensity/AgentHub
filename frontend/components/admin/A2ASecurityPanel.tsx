'use client';

// A2A Security Panel (Sprint L4)
// Displays TLS configuration status, agent signature verification results,
// and provides controls to re-verify all agent signatures.

import { useState, useEffect, useCallback, type JSX } from 'react';
import { useA2AStore } from '../../stores/a2aStore';

interface TLSStatus {
  enabled: boolean;
  strict_verify: boolean;
  cert_file: string;
  key_file: string;
  ca_file: string;
  cert_expiry?: string;
  cert_subject?: string;
  cert_valid?: boolean;
}

interface SignatureVerificationResult {
  url: string;
  name: string;
  status: 'verified' | 'unsigned' | 'invalid';
  message: string;
}

const BASE = '/platform/a2a';

async function api(path: string, options: RequestInit = {}): Promise<Response> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    ...options,
  });
  return res;
}

function getAuthHeaders(): Record<string, string> {
  try {
    const stored = localStorage.getItem('auth-storage');
    if (stored) {
      const parsed = JSON.parse(stored);
      const token = parsed?.state?.token || parsed?.token;
      if (token) return { Authorization: `Bearer ${token}` };
    }
  } catch { /* ignore */ }
  return {};
}

function StatusPill({ status }: { status: string }): JSX.Element {
  const map: Record<string, { label: string; cls: string }> = {
    verified: { label: '已验证', cls: 'bg-green-100 text-green-700 border-green-200' },
    unsigned: { label: '未签名', cls: 'bg-amber-100 text-amber-700 border-amber-200' },
    invalid: { label: '无效', cls: 'bg-red-100 text-red-700 border-red-200' },
    active: { label: '已启用', cls: 'bg-green-100 text-green-700 border-green-200' },
    disabled: { label: '未启用', cls: 'bg-warm-100 text-warm-500 border-warm-200' },
  };
  const s = map[status] || { label: status, cls: 'bg-warm-50 text-warm-500 border-warm-150' };
  return <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full border ${s.cls}`}>{s.label}</span>;
}

export default function A2ASecurityPanel(): JSX.Element {
  const { agents } = useA2AStore();
  const [tlsStatus, setTlsStatus] = useState<TLSStatus | null>(null);
  const [tlsLoading, setTlsLoading] = useState(false);
  const [sigResults, setSigResults] = useState<SignatureVerificationResult[]>([]);
  const [sigLoading, setSigLoading] = useState(false);

  const loadTlsStatus = useCallback(async () => {
    setTlsLoading(true);
    try {
      const res = await api('/tls-status');
      const data = await res.json();
      if (res.ok) setTlsStatus(data);
    } catch {
      setTlsStatus({ enabled: false, strict_verify: false, cert_file: '', key_file: '', ca_file: '' });
    } finally {
      setTlsLoading(false);
    }
  }, []);

  const verifySignatures = useCallback(async () => {
    setSigLoading(true);
    try {
      const res = await api('/registry/verify-signatures', { method: 'POST' });
      const data = await res.json();
      if (res.ok) setSigResults(data.results || []);
    } catch {
      setSigResults([]);
    } finally {
      setSigLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadTlsStatus();
  }, [loadTlsStatus]);

  // Compute signature status summary from registered agents
  const verifiedCount = sigResults.filter((r) => r.status === 'verified').length;
  const unsignedCount = sigResults.filter((r) => r.status === 'unsigned').length;
  const invalidCount = sigResults.filter((r) => r.status === 'invalid').length;

  return (
    <div className="space-y-6">
      {/* ── TLS Configuration ──────────────────────────────────────── */}
      <div className="rounded-xl border border-warm-200 bg-white p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-primary-500">lock</span>
            <h4 className="text-sm font-semibold text-warm-900">TLS 连接配置</h4>
          </div>
          {tlsStatus && (
            <StatusPill status={tlsStatus.enabled ? 'active' : 'disabled'} />
          )}
        </div>

        {tlsLoading ? (
          <div className="space-y-2">
            <div className="skeleton skeleton-text h-4 w-full" />
            <div className="skeleton skeleton-text h-4 w-3/4" />
          </div>
        ) : tlsStatus ? (
          <div className="space-y-3">
            {/* Config summary */}
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-lg bg-warm-50 p-3">
                <span className="text-[10px] text-warm-400 uppercase tracking-wider block mb-1">TLS</span>
                <span className="text-sm font-medium text-warm-800">
                  {tlsStatus.enabled ? '已启用' : '未启用'}
                </span>
              </div>
              <div className="rounded-lg bg-warm-50 p-3">
                <span className="text-[10px] text-warm-400 uppercase tracking-wider block mb-1">严格验证</span>
                <span className="text-sm font-medium text-warm-800">
                  {tlsStatus.strict_verify ? '开启' : '关闭'}
                </span>
              </div>
            </div>

            {/* Certificate info */}
            {tlsStatus.enabled && (
              <>
                <div className="rounded-lg border border-warm-150 bg-warm-50 p-3">
                  <h5 className="text-xs font-semibold text-warm-500 uppercase tracking-wider mb-2">客户端证书</h5>
                  <div className="space-y-1 text-xs">
                    <div className="flex items-center gap-2">
                      <span className="text-warm-400 w-16 shrink-0">Cert:</span>
                      <code className="text-warm-700 font-mono truncate">{tlsStatus.cert_file || '未配置'}</code>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-warm-400 w-16 shrink-0">Key:</span>
                      <code className="text-warm-700 font-mono truncate">{tlsStatus.key_file || '未配置'}</code>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-warm-400 w-16 shrink-0">CA:</span>
                      <code className="text-warm-700 font-mono truncate">{tlsStatus.ca_file || '未配置'}</code>
                    </div>
                  </div>
                </div>

                {/* Cert validity */}
                {tlsStatus.cert_subject && (
                  <div className="rounded-lg border border-primary-150 bg-primary-50/50 p-3">
                    <h5 className="text-xs font-semibold text-warm-500 uppercase tracking-wider mb-2">证书信息</h5>
                    <div className="space-y-1 text-xs">
                      <div className="flex items-center gap-2">
                        <span className="text-warm-400 w-16 shrink-0">Subject:</span>
                        <code className="text-warm-700 font-mono truncate">{tlsStatus.cert_subject}</code>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-warm-400 w-16 shrink-0">到期:</span>
                        <span className="text-warm-700">{tlsStatus.cert_expiry || '未知'}</span>
                        {tlsStatus.cert_valid !== undefined && (
                          <StatusPill status={tlsStatus.cert_valid ? 'verified' : 'invalid'} />
                        )}
                      </div>
                    </div>
                  </div>
                )}
              </>
            )}

            {/* Refresh button */}
            <button
              className="btn-primary text-xs px-3 py-1.5"
              onClick={() => void loadTlsStatus()}
              disabled={tlsLoading}
            >
              {tlsLoading ? (
                <span className="material-symbols-outlined text-sm animate-spin">progress_activity</span>
              ) : '刷新 TLS 状态'}
            </button>
          </div>
        ) : (
          <div className="text-center py-8">
            <span className="material-symbols-outlined text-3xl text-warm-300 mb-2 block">cloud_off</span>
            <p className="text-sm text-warm-500">无法获取 TLS 配置状态</p>
          </div>
        )}
      </div>

      {/* ── Signature Verification ─────────────────────────────────── */}
      <div className="rounded-xl border border-warm-200 bg-white p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-primary-500">verified_user</span>
            <h4 className="text-sm font-semibold text-warm-900">Agent Card 签名验证</h4>
          </div>
          <button
            className="btn-primary text-xs px-3 py-1.5 flex items-center gap-1"
            onClick={() => void verifySignatures()}
            disabled={sigLoading}
          >
            {sigLoading ? (
              <span className="material-symbols-outlined text-sm animate-spin">progress_activity</span>
            ) : (
              <span className="material-symbols-outlined text-sm">refresh</span>
            )}
            重新验证全部
          </button>
        </div>

        {/* Summary stats */}
        {sigResults.length > 0 && (
          <div className="grid grid-cols-3 gap-2 mb-4">
            <div className="rounded-lg bg-green-50 border border-green-100 px-3 py-2 text-center">
              <div className="text-lg font-bold text-green-700">{verifiedCount}</div>
              <div className="text-[10px] text-green-600">已验证</div>
            </div>
            <div className="rounded-lg bg-amber-50 border border-amber-100 px-3 py-2 text-center">
              <div className="text-lg font-bold text-amber-700">{unsignedCount}</div>
              <div className="text-[10px] text-amber-600">未签名</div>
            </div>
            <div className="rounded-lg bg-red-50 border border-red-100 px-3 py-2 text-center">
              <div className="text-lg font-bold text-red-700">{invalidCount}</div>
              <div className="text-[10px] text-red-600">无效</div>
            </div>
          </div>
        )}

        {/* Agent list with signature status */}
        {sigLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="skeleton skeleton-text h-12 rounded-lg" />
            ))}
          </div>
        ) : sigResults.length > 0 ? (
          <div className="space-y-1.5 max-h-[400px] overflow-y-auto">
            {sigResults.map((result) => (
              <div
                key={result.url}
                className="flex items-center justify-between rounded-lg border border-warm-150 bg-warm-50 px-3 py-2.5"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-warm-800 truncate">{result.name}</span>
                    <span className="text-[10px] text-warm-400 font-mono truncate">{result.url}</span>
                  </div>
                  <p className="text-[11px] text-warm-500 mt-0.5">{result.message}</p>
                </div>
                <div className="shrink-0 ml-3">
                  <StatusPill status={result.status} />
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-center py-8">
            <span className="material-symbols-outlined text-3xl text-warm-300 mb-2 block">description</span>
            <p className="text-sm text-warm-500">点击"重新验证全部"以检查所有注册 Agent 的签名状态</p>
          </div>
        )}

        {/* Agent list without signature verification (from store) */}
        {sigResults.length === 0 && !sigLoading && agents.length > 0 && (
          <div className="mt-3 border-t border-warm-150 pt-3">
            <h5 className="text-xs font-semibold text-warm-500 uppercase tracking-wider mb-2">
              已注册 Agent ({agents.length})
            </h5>
            <div className="space-y-1.5 max-h-[300px] overflow-y-auto">
              {agents.map((agent) => (
                <div
                  key={agent.url}
                  className="flex items-center justify-between rounded-lg border border-warm-150 bg-warm-50 px-3 py-2"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-warm-800 truncate">{agent.name}</span>
                      <span className="text-[10px] text-warm-400 font-mono truncate">{agent.url}</span>
                    </div>
                  </div>
                  {agent.source === 'internal' ? (
                    <StatusPill status="verified" />
                  ) : (
                    <StatusPill status="unsigned" />
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
