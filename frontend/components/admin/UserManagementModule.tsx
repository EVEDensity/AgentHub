import { type FormEvent, type JSX } from 'react';
import type { User } from '../../types';
import { StatCard, MiniStatBox } from './StatCard';

const ROLE_LABELS: Record<string, string> = { admin: '管理员', developer: '开发者', viewer: '观察者' };
const ROLE_COLORS: Record<string, string> = { admin: 'text-red-600 bg-red-50', developer: 'text-teal-600 bg-teal-50', viewer: 'text-warm-600 bg-warm-50' };

interface TokenData {
  range: { start: string; end: string };
  today: { sessions: number; messages: number; tokens: number };
  yesterday: { sessions: number; messages: number; tokens: number };
  last30: { sessions: number; messages: number; tokens: number };
  days: Array<{ date: string; sessions: number; messages: number; tokens: number }>;
  generatedAt: string;
}

export interface UserManagementModuleProps {
  user: User | null;
  authHeaders: () => Record<string, string>;
  setNotice: (msg: string) => void;
  fmtErr: (detail: unknown, fallback: string) => string;
  // Token state
  tokenData: TokenData | null;
  tokenLoading: boolean;
  tokenError: string;
  // Profile state
  profileBio: string;
  profileEditingField: string | null;
  profileFieldDraft: string;
  profileLocation: string;
  profileEmail: string;
  profileOrg: string;
  profileAvatarUrl: string;
  profileUploading: boolean;
  // User mgmt state
  userList: Array<{ id: string; name: string; role: string; created_at: string }>;
  userListLoading: boolean;
  userListError: string;
  newUserName: string;
  newUserPassword: string;
  newUserRole: string;
  creatingUser: boolean;
  // Setters & actions
  setProfileEditingField: (v: string | null) => void;
  setProfileFieldDraft: (v: string) => void;
  setNewUserName: (v: string) => void;
  setNewUserPassword: (v: string) => void;
  setNewUserRole: (v: string) => void;
  handleStartEditField: (field: string, currentValue: string) => void;
  handleSaveField: () => Promise<void>;
  handleCancelEditField: () => void;
  handleUploadProfileAvatar: (e: React.ChangeEvent<HTMLInputElement>) => void;
  handleCreateUser: (e: FormEvent) => Promise<void>;
  handleChangeUserRole: (userId: string, newRole: string) => Promise<void>;
  handleDeleteUser: (userId: string, userName: string) => Promise<void>;
  loadTokenUsage: () => Promise<void>;
}

// Heatmap helpers
function buildWeeks(days: Array<{ date: string; sessions: number; messages: number; tokens: number }>): (typeof days[0] | null)[][] {
  if (!days.length) return [];
  const firstDate = new Date(days[0].date);
  const dayOfWeek = (firstDate.getDay() + 6) % 7;
  const padded: (typeof days[0] | null)[] = Array(dayOfWeek).fill(null).concat(days);
  const weeks: (typeof days[0] | null)[][] = [];
  for (let i = 0; i < padded.length; i += 7) weeks.push(padded.slice(i, i + 7));
  return weeks;
}

function getLevel(tokens: number, max: number): number {
  if (tokens === 0 || max === 0) return 0;
  const r = tokens / max;
  if (r <= 0.15) return 1; if (r <= 0.35) return 2; if (r <= 0.55) return 3; if (r <= 0.75) return 4; return 5;
}

const HEAT = ['bg-[#e8e6e1]', 'bg-[#d4cfc6]', 'bg-[#b0a89a]', 'bg-[#8c8170]', 'bg-[#6b5f4f]', 'bg-[#403a32]'];

export default function UserManagementModule(props: UserManagementModuleProps): JSX.Element {
  const maxTokens = props.tokenData?.days.length
    ? Math.max(...props.tokenData.days.map((d) => d.tokens))
    : 0;
  const weeks = props.tokenData ? buildWeeks(props.tokenData.days) : [];

  return (
    <section className="space-y-6">
      {/* ══════ Hero: Profile Card (left) + Token Stats (right) ══════ */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
        {/* ── Left: User Profile Card ── */}
        <div className="lg:col-span-2 rounded-2xl border border-warm-200 bg-warm-100 overflow-hidden shadow-sm">
          <div className="h-20 bg-gradient-to-r from-teal-500 to-cyan-600" />
          <div className="px-6 pb-6 relative">
            <div className="flex items-end -mt-10 mb-3">
              <div className="relative group">
                {props.profileAvatarUrl ? (
                  <img src={props.profileAvatarUrl} className="h-20 w-20 rounded-full object-cover border-4 border-white shadow-sm ring-1 ring-warm-150" alt={props.user?.name || ''} />
                ) : (
                  <span className="flex h-20 w-20 rounded-full items-center justify-center border-4 border-white shadow-sm ring-1 ring-warm-150 text-2xl font-bold text-white select-none"
                    style={{ background: 'linear-gradient(135deg, #14b8a6, #06b6d4)' }}>
                    {(props.user?.name || 'U')[0].toUpperCase()}
                  </span>
                )}
                <label className="absolute inset-0 flex items-center justify-center rounded-full bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer" title="点击更换头像">
                  {props.profileUploading ? (
                    <div className="h-5 w-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  ) : (
                    <svg className="h-5 w-5 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" />
                      <circle cx="12" cy="13" r="4" />
                    </svg>
                  )}
                  <input type="file" accept="image/*" className="hidden" onChange={(e) => { props.handleUploadProfileAvatar(e); }} />
                </label>
              </div>
            </div>

            <div className="mb-1">
              <h2 className="text-xl font-bold text-warm-900 leading-tight">{props.user?.name || '用户'}</h2>
              <span className={`inline-block mt-0.5 text-xs font-semibold px-2 py-0.5 rounded-full ${props.user?.role === 'admin' ? 'bg-red-100 text-red-700' : props.user?.role === 'developer' ? 'bg-blue-100 text-blue-700' : 'bg-warm-100 text-warm-600'}`}>
                {ROLE_LABELS[props.user?.role || ''] || props.user?.role || '开发者'}
              </span>
            </div>

            {/* Bio */}
            <div className="mt-3 mb-4">
              {props.profileEditingField === 'bio' ? (
                <div className="space-y-2">
                  <textarea className="w-full resize-none rounded-lg border border-teal-300 bg-teal-50/30 px-3 py-2 text-sm text-warm-800 outline-none focus:ring-2 focus:ring-teal-200" rows={2} value={props.profileFieldDraft} onChange={(e) => props.setProfileFieldDraft(e.target.value)} autoFocus />
                  <div className="flex gap-2">
                    <button className="text-xs px-3 py-1 rounded-md bg-teal-600 text-white hover:bg-teal-700 transition-colors" onClick={() => { void props.handleSaveField(); }}>保存</button>
                    <button className="text-xs px-3 py-1 rounded-md border border-warm-200 text-warm-500 hover:bg-warm-50 transition-colors" onClick={props.handleCancelEditField}>取消</button>
                  </div>
                </div>
              ) : (
                <div className="group relative">
                  <p className="text-sm text-warm-600 leading-relaxed">{props.profileBio || '暂无简介 — 介绍一下自己吧'}</p>
                  <button className="absolute -right-1 -top-1 p-1 rounded opacity-0 group-hover:opacity-100 transition-opacity text-warm-400 hover:text-teal-600 hover:bg-teal-50" onClick={() => props.handleStartEditField('bio', props.profileBio)} title="编辑简介">
                    <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                  </button>
                </div>
              )}
            </div>

            {/* Detail items */}
            <DetailItem icon={LocationIcon} label="所在地" value={props.profileLocation} placeholder="点击设置所在地" field="location" {...props} />
            <DetailItem icon={EmailIcon} label="邮箱" value={props.profileEmail} placeholder="点击设置邮箱" field="email" {...props} />
            <DetailItem icon={OrgIcon} label="组织" value={props.profileOrg} placeholder="点击设置组织" field="org" {...props} />

            <div className="flex items-center gap-2.5 text-sm text-warm-600 mt-2.5">
              <svg className="h-4 w-4 shrink-0 text-warm-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>
              </svg>
              <span>加入于 {props.user?.created_at ? new Date(props.user.created_at).toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' }) : '—'}</span>
            </div>
          </div>
        </div>

        {/* ── Right: Token Usage Statistics ── */}
        <div className="lg:col-span-3 space-y-5">
          {props.tokenLoading ? (
            <div className="grid grid-cols-3 gap-3">
              {[0, 1, 2].map((i) => (
                <div key={i} className="rounded-xl border border-warm-150 bg-warm-100 p-4 animate-pulse">
                  <div className="h-3 w-12 bg-warm-150 rounded mb-2" />
                  <div className="h-6 w-24 bg-warm-100 rounded" />
                </div>
              ))}
            </div>
          ) : props.tokenData ? (
            <>
              <div className="grid grid-cols-3 gap-3">
                <StatCard label="今日" tokens={props.tokenData.today.tokens} sessions={props.tokenData.today.sessions} messages={props.tokenData.today.messages} />
                <StatCard label="昨日" tokens={props.tokenData.yesterday.tokens} sessions={props.tokenData.yesterday.sessions} messages={props.tokenData.yesterday.messages} />
                <StatCard label="30 天" tokens={props.tokenData.last30.tokens} sessions={props.tokenData.last30.sessions} messages={props.tokenData.last30.messages} />
              </div>

              {/* Token heatmap */}
              <div className="rounded-2xl border border-warm-200 bg-warm-100 overflow-hidden">
                <div className="border-b border-warm-100 px-5 py-3 flex items-center justify-between">
                  <div>
                    <h3 className="text-sm font-semibold text-warm-900">Token 用量热力图</h3>
                    <p className="text-xs text-warm-500 mt-0.5">
                      {props.tokenData.range.start} — {props.tokenData.range.end} · 共 {props.tokenData.days.length} 天
                    </p>
                  </div>
                  <button className="btn-ghost text-xs px-2 py-1 rounded" onClick={() => { void props.loadTokenUsage(); }}>
                    <span className="material-symbols-outlined text-[14px]">refresh</span> 刷新
                  </button>
                </div>
                <div className="px-5 py-4 overflow-x-auto">
                  <div className="flex gap-1" style={{ minWidth: weeks.length * 14 }}>
                    {weeks.map((week, wi) => (
                      <div key={wi} className="flex flex-col gap-1">
                        {week.map((day, di) => (
                          <div
                            key={di}
                            className={`h-3 w-3 rounded-sm ${day ? HEAT[getLevel(day.tokens, maxTokens)] : 'bg-transparent'}`}
                            title={day ? `${day.date}: ${day.tokens} tokens · ${day.messages} 条消息 · ${day.sessions} 个会话` : ''}
                          />
                        ))}
                      </div>
                    ))}
                  </div>
                  <div className="flex items-center justify-end gap-2 mt-3 text-[10px] text-warm-400">
                    <span>少</span>
                    {HEAT.map((cls, i) => (
                      <div key={i} className={`h-2.5 w-2.5 rounded-sm ${cls}`} />
                    ))}
                    <span>多</span>
                  </div>
                </div>
              </div>
            </>
          ) : props.tokenError ? (
            <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-600">
              {props.tokenError}
              <button className="ml-2 underline" onClick={() => { void props.loadTokenUsage(); }}>重试</button>
            </div>
          ) : null}
        </div>
      </div>

      {/* ══════ User Management Table ══════ */}
      {props.user?.role === 'admin' && (
        <section className="rounded-2xl border border-warm-200 bg-warm-100 overflow-hidden">
          <div className="border-b border-warm-100 px-5 py-3 flex items-center justify-between">
            <div>
              <h3 className="text-base font-semibold text-warm-900">用户管理</h3>
              <p className="text-xs text-warm-500 mt-0.5">管理系统用户、角色和权限。</p>
            </div>
          </div>

          {/* Create user form */}
          <div className="border-b border-warm-100 bg-warm-50/50 px-5 py-4">
            <form className="flex items-end gap-3 flex-wrap" onSubmit={(e) => { void props.handleCreateUser(e); }}>
              <div className="flex flex-col gap-1">
                <label className="text-xs font-medium text-warm-500">用户名</label>
                <input className="input-field min-w-[140px]" placeholder="用户名" value={props.newUserName} onChange={(e) => props.setNewUserName(e.target.value)} required />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-xs font-medium text-warm-500">密码</label>
                <input className="input-field min-w-[140px]" type="password" placeholder="密码" value={props.newUserPassword} onChange={(e) => props.setNewUserPassword(e.target.value)} required />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-xs font-medium text-warm-500">角色</label>
                <select className="input-field min-w-[120px]" value={props.newUserRole} onChange={(e) => props.setNewUserRole(e.target.value)}>
                  <option value="developer">开发者</option>
                  <option value="viewer">观察者</option>
                  <option value="admin">管理员</option>
                </select>
              </div>
              <button type="submit" className="btn-primary" disabled={props.creatingUser}>
                {props.creatingUser ? '创建中...' : '创建用户'}
              </button>
            </form>
          </div>

          {/* User table */}
          <div className="overflow-x-auto">
            {props.userListLoading ? (
              <div className="flex justify-center py-8"><div className="h-5 w-5 animate-spin rounded-full border-2 border-warm-400 border-t-transparent" /></div>
            ) : props.userListError ? (
              <div className="px-5 py-4 text-sm text-red-500">{props.userListError}</div>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-warm-150 text-left text-xs font-medium text-warm-500">
                    <th className="px-5 py-2.5">用户名</th>
                    <th className="px-5 py-2.5">角色</th>
                    <th className="px-5 py-2.5">注册时间</th>
                    <th className="px-5 py-2.5 text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {props.userList.map((u) => (
                    <tr key={u.id} className="border-b border-warm-50 hover:bg-warm-50 transition-colors">
                      <td className="px-5 py-2.5 font-medium text-warm-800">
                        {u.name}
                        {u.id === props.user?.id && <span className="ml-1.5 text-[10px] text-teal-500 font-normal">(你)</span>}
                      </td>
                      <td className="px-5 py-2.5">
                        {props.user?.role === 'admin' && u.id !== props.user?.id ? (
                          <select
                            value={u.role}
                            onChange={(e) => { void props.handleChangeUserRole(u.id, e.target.value); }}
                            className={`rounded-full px-2.5 py-0.5 text-xs font-medium border-0 outline-none ${ROLE_COLORS[u.role] || 'text-warm-600 bg-warm-50'}`}
                          >
                            <option value="admin">管理员</option>
                            <option value="developer">开发者</option>
                            <option value="viewer">观察者</option>
                          </select>
                        ) : (
                          <span className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${ROLE_COLORS[u.role] || 'text-warm-600 bg-warm-50'}`}>
                            {ROLE_LABELS[u.role] || u.role}
                          </span>
                        )}
                      </td>
                      <td className="px-5 py-2.5 text-warm-400">{u.created_at ? new Date(u.created_at).toLocaleString('zh-CN') : '-'}</td>
                      <td className="px-5 py-2.5 text-right">
                        {props.user?.role === 'admin' && u.id !== props.user?.id && (
                          <button className="text-xs text-red-500 hover:text-red-700 hover:bg-red-50 rounded px-2 py-1 transition-colors" onClick={() => { void props.handleDeleteUser(u.id, u.name); }}>
                            删除
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>
      )}
    </section>
  );
}

// ── Inline SVG icon components ──
const LocationIcon = (
  <svg className="h-4 w-4 shrink-0 text-warm-400 group-hover:text-teal-500 transition-colors" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z" /><circle cx="12" cy="10" r="3" />
  </svg>
);
const EmailIcon = (
  <svg className="h-4 w-4 shrink-0 text-warm-400 group-hover:text-teal-500 transition-colors" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/>
  </svg>
);
const OrgIcon = (
  <svg className="h-4 w-4 shrink-0 text-warm-400 group-hover:text-teal-500 transition-colors" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>
  </svg>
);
const EditIcon = (
  <svg className="h-3 w-3 shrink-0 text-warm-300 opacity-0 group-hover:opacity-100 transition-opacity" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
);

// Reusable detail item component
function DetailItem(
  props: UserManagementModuleProps & {
    icon: JSX.Element;
    label: string;
    value: string;
    placeholder: string;
    field: string;
  }
): JSX.Element {
  if (props.profileEditingField === props.field) {
    return (
      <div className="flex items-center gap-2 mt-2.5">
        {props.icon}
        <input
          className="flex-1 rounded border border-teal-300 bg-teal-50/30 px-2 py-1 text-sm text-warm-800 outline-none focus:ring-2 focus:ring-teal-200"
          value={props.profileFieldDraft}
          onChange={(e) => props.setProfileFieldDraft(e.target.value)}
          placeholder={`输入${props.label}...`}
          autoFocus
        />
        <button className="text-xs px-2 py-1 rounded bg-teal-600 text-white hover:bg-teal-700 transition-colors" onClick={() => { void props.handleSaveField(); }}>保存</button>
        <button className="text-xs px-2 py-1 rounded border border-warm-200 text-warm-500 hover:bg-warm-50 transition-colors" onClick={props.handleCancelEditField}>取消</button>
      </div>
    );
  }

  return (
    <div className="group flex items-center gap-2.5 text-sm text-warm-600 mt-2.5 cursor-pointer" onClick={() => props.handleStartEditField(props.field, props.value)} title={`点击编辑${props.label}`}>
      {props.icon}
      <span className="group-hover:text-teal-600 transition-colors">{props.value || props.placeholder}</span>
      {EditIcon}
    </div>
  );
}
