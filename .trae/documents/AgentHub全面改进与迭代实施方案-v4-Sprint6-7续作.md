# AgentHub 全面改进与迭代实施方案 v4 — Sprint 6/7 续作

> 本文档是 v3 计划的延续，聚焦剩余两项工作：**Sprint 6 (P1.2 Next.js 14 + App Router 根路由迁移)** 与 **Sprint 7 (集成测试 + 文档收尾)**。
> Sprint 5 (P0.2 pluggy 插件系统) 已在前序会话中全部完成（32 个测试通过），本计划不再重复。

---

## 一、当前状态分析

### 1.1 已完成（Sprint 5 — P0.2 pluggy 插件系统）✅

经探索验证，以下文件均已存在且实现完整：

| 文件 | 状态 | 关键实现 |
|------|------|---------|
| `app/services/tools/plugin_manager.py` | ✅ | `PluginManager` 类、`load_all()`、`_HOOK_METHOD_NAMES` 方法名匹配 |
| `app/services/tools/plugins/__init__.py` | ✅ | 导出 AuditPlugin/PermissionPlugin/SanitizePlugin |
| `app/services/tools/plugins/builtin_audit.py` | ✅ | `@hookimpl post_tool_use`，best-effort 写审计日志 |
| `app/services/tools/plugins/builtin_permission.py` | ✅ | `TOOL_RISK` 字典，high-risk 需 `tool:execute:high` scope |
| `app/services/tools/plugins/builtin_sanitize.py` | ✅ | 懒加载 `OutputSanitizer`，扫描 6 个输出字段 |
| `app/services/tools/hooks.py` | ✅ | 双轨兼容层（pluggy 同步 + legacy 异步） |
| `app/services/tools/plugin_spec.py` | ✅ | 4 个 hookspec，`HOOK_NAMESPACE="agenthub"` |
| `plugins/example_plugin/` | ✅ | 示例插件（计数器） |
| `plugins/README.md` + `docs/plugin-development.md` | ✅ | 快速入门 + 完整开发文档 |
| `app/services/tools/test_plugin_manager.py` | ✅ | 32 个测试全部通过 |

### 1.2 待完成（Sprint 6 — P1.2 Next.js 14 + App Router）🔄

**关键发现**（经 Phase 1 探索确认）：

1. **根路由未迁移**：`frontend/app/page.tsx` **不存在**，根路由 `/` 仍由 `frontend/pages/index.tsx`（2870 行 `AgentHubIM` 组件）服务。
2. **app/ 目录已有多个子路由**：`admin/`（含 page/layout/error/loading）、`app/[botId]/`、`canvas/`、`chat/`、`charts/` — 这些已迁移完成，**仅缺根路由**。
3. **package.json 版本落后**：`next ^13.5.7`、`react ^18.2.0`、`react-dom ^18.2.0`、`@next/bundle-analyzer ^13.5.7`。
4. **next.config.js 缺 transpilePackages**：未声明 `@monaco-editor/react`、`react-konva`、`konva`、`framer-motion` 等 ESM 包，App Router 下可能构建告警。
5. **app/layout.tsx 缺主题 FOUC 脚本**：当前只有 reduced-motion 脚本，缺少从 `localStorage` 读取 `agenthub_theme` 并应用到 `<html data-theme>` 的阻塞脚本（该脚本当前在 `pages/_document.tsx`）。
6. **app/layout.tsx 缺 ErrorBoundary**：`pages/_app.tsx` 有 class-based ErrorBoundary，App Router 需用原生 `app/error.tsx`。
7. **app/admin/error.tsx 已存在**：可作为根 `app/error.tsx` 的模式参考（`'use client'` + `ErrorProps { error, reset }`）。

### 1.3 关键架构事实

- `frontend/app/` 与 `frontend/pages/` 是**同级目录**（都在 `frontend/` 下），因此 `pages/index.tsx` 中的相对路径（`../components`、`../lib`、`../types`、`../hooks`、`../stores`、`../styles`）在 `app/page.tsx` 中**完全相同**，无需修改。
- `next.config.js` 的 `rewrites()` 将 `/api/*` 路由到 Go gateway（8081），`/platform/*` 路由到 Go gateway — 迁移不影响 API 路由。
- `next.config.js` 已有 `typescript: { ignoreBuildErrors: true }` — TS 类型错误不会阻塞构建。

---

## 二、Sprint 6：Next.js 14 升级 + App Router 根路由迁移

### S6.1 — 升级 package.json 依赖版本

**文件**：`frontend/package.json`

**改动**：
```json
"next": "^14.2.15",                    // 从 ^13.5.7
"react": "^18.3.1",                    // 从 ^18.2.0
"react-dom": "^18.3.1",                // 从 ^18.2.0
"@next/bundle-analyzer": "^14.2.15"    // 从 ^13.5.7（devDependencies）
```

**为什么**：
- Next.js 14.2 是 LTS 稳定版，App Router 已成熟，避免 15.x 的 React 19 破坏性变更（konva/react-konva/framer-motion 兼容性）。
- React 18.3.1 是 18.x 最后一个 minor，包含 19 的弃用警告，过渡平滑。
- `@next/bundle-analyzer` 必须与 next 主版本对齐，否则 `withBundleAnalyzer` 包装器报错。

**为什么不升级到 Next.js 15**：React 19 尚未被 `react-konva@18.2.10`、`framer-motion@12.x`（虽已支持但偶发问题）、`@monaco-editor/react@4.6.0` 完全验证，保守选择 14.2。

### S6.2 — 创建 app/page.tsx（根路由机械迁移）

**文件**：`frontend/app/page.tsx`（新建）

**方法**：读取 `frontend/pages/index.tsx` 全部内容，创建 `app/page.tsx`，内容 = `'use client';\n\n` + pages/index.tsx 原始内容（去掉原文件首行若已是 import）。

**关键约束**：
- `'use client'` 指令**必须在文件第一行**（App Router 客户端组件要求）。
- 相对路径 `../components`、`../lib`、`../types`、`../hooks`、`../stores`、`../styles` **无需修改**（app/ 和 pages/ 同级）。
- `export default function AgentHubIM()` 保留原函数名和签名。
- 所有 `dynamic(() => import('../components/...'), { ssr: false })` 保持不变。
- 不重写任何业务逻辑——这是**机械迁移**。

**实现方式**：因 pages/index.tsx 有 2870 行，使用 Shell 命令 `copy` + 前置 `'use client'` 行，再用 Read 验证首尾正确。

**PowerShell 命令**：
```powershell
# 1. 创建带 'use client' 前缀的新文件
$content = Get-Content -Raw "d:\Users\xyn\Desktop\agenthub\AgenthubV1.2\frontend\pages\index.tsx"
"'use client';`n`n$content" | Set-Content -NoNewline "d:\Users\xyn\Desktop\agenthub\AgenthubV1.2\frontend\app\page.tsx" -Encoding UTF8
```

**验证**：
- Read `app/page.tsx` 前 5 行，确认 `'use client';` 在第 1 行，原 import 在第 3 行起。
- Read `app/page.tsx` 后 5 行，确认 `export default function AgentHubIM` 完整。

### S6.3 — 创建 app/error.tsx（根错误边界）

**文件**：`frontend/app/error.tsx`（新建）

**设计**：参考 `app/admin/error.tsx` 的模式（`'use client'` + `ErrorProps { error, reset }`），但增强为全屏错误页（因为根路由是主应用入口），保留 `pages/_app.tsx` 中 ErrorBoundary 的诊断信息展示（message + stack）。

```tsx
'use client';

import type { JSX } from 'react';

interface ErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

export default function RootError({ error, reset }: ErrorProps): JSX.Element {
  return (
    <div style={{
      padding: 40,
      fontFamily: 'monospace',
      background: '#121418',
      minHeight: '100vh',
      color: '#F87272',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 16,
    }}>
      <span className="material-symbols-outlined" style={{ fontSize: 64, color: '#F87272' }}>
        error_outline
      </span>
      <h1 style={{ fontSize: 24, marginBottom: 16, color: '#E4E7EC' }}>
        应用发生错误
      </h1>
      <div style={{
        background: '#191C22',
        border: '1px solid #F87272',
        padding: 20,
        marginBottom: 16,
        maxWidth: 800,
        width: '100%',
      }}>
        <strong>错误信息：</strong> {error.message || '未知错误'}
        {error.digest && (
          <div style={{ marginTop: 8, fontSize: 12, color: '#9CA3AF' }}>
            错误 ID: {error.digest}
          </div>
        )}
      </div>
      <div style={{ maxWidth: 800, width: '100%' }}>
        <strong>调用栈：</strong>
        <pre style={{
          background: '#191C22',
          color: '#E4E7EC',
          padding: 16,
          borderRadius: 6,
          overflow: 'auto',
          maxHeight: 300,
          fontSize: 12,
          lineHeight: 1.5,
        }}>
          {error.stack}
        </pre>
      </div>
      <button
        onClick={reset}
        style={{
          marginTop: 8,
          padding: '10px 24px',
          borderRadius: 8,
          background: '#3B82F6',
          color: 'white',
          border: 'none',
          cursor: 'pointer',
          fontSize: 14,
        }}
      >
        重试
      </button>
    </div>
  );
}
```

**为什么**：App Router 没有 `_app.tsx`，错误边界必须用 `app/error.tsx`（必须是客户端组件）。根路由错误页需要比 admin 子路由更详细（含 stack），因为主应用崩溃时需要诊断信息。

### S6.4 — 更新 app/layout.tsx（添加主题 FOUC 脚本）

**文件**：`frontend/app/layout.tsx`（修改）

**改动**：在 `<head>` 内、reduced-motion 脚本**之前**，插入从 `pages/_document.tsx` 迁移来的主题 FOUC 阻塞脚本。

**插入位置**：`<link rel="stylesheet" ...>` 之后、reduced-motion `<script>` 之前。

**插入内容**：
```tsx
{/* Theme FOUC prevention: read stored theme from localStorage and apply
    data-theme to <html> BEFORE first paint. Must run synchronously. */}
<script
  dangerouslySetInnerHTML={{
    __html: `
      (function() {
        try {
          var t = localStorage.getItem('agenthub_theme');
          if (!t) t = localStorage.getItem('agenthub_theme_legacy');
          if (t === 'dark' || t === 'light' || t === 'warm') {
            document.documentElement.setAttribute('data-theme', t);
            document.documentElement.style.colorScheme = t === 'dark' ? 'dark' : 'light';
          }
        } catch(e) {}
      })();
    `,
  }}
/>
```

**为什么**：
- App Router 没有 `_document.tsx`，主题脚本必须内联到 `app/layout.tsx` 的 `<head>`。
- 必须在 reduced-motion 脚本之前，因为主题切换可能影响动画偏好。
- 必须用 `dangerouslySetInnerHTML` 内联（不能用 React useEffect，否则会在 hydration 后才执行，导致 FOUC）。

### S6.5 — 更新 next.config.js（添加 transpilePackages）

**文件**：`frontend/next.config.js`（修改）

**改动**：在 `nextConfig` 对象中添加 `transpilePackages` 字段（位于 `compress: true` 之后、`typescript` 之前）：

```js
// Transpile ESM packages that ship untranspiled source (required by App Router
// for proper server/client boundary handling).
transpilePackages: [
  '@monaco-editor/react',
  'react-konva',
  'konva',
  'framer-motion',
  'react-syntax-highlighter',
  'react-diff-viewer-continued',
],
```

**为什么**：
- App Router 对 ESM 包的转译更严格，未声明的 ESM 包会导致 "Module not found" 或样式闪烁。
- 这 6 个包都发布 ESM 源码，Next.js 13 Pages Router 下可能侥幸工作，但 14 App Router 下必须显式转译。
- 不转译 `react-markdown`/`remark-gfm`（它们已正确发布 CJS+ESM dual bundle）。

### S6.6 — 删除 pages/ 残留

**文件**：删除 `frontend/pages/index.tsx`、`frontend/pages/_app.tsx`、`frontend/pages/_document.tsx`

**前置条件**：S6.1-S6.5 全部完成且 `npm run build` 通过。

**为什么**：
- App Router 和 Pages Router 不能同时服务根路由 `/`（会冲突或 Next.js 优先用 Pages Router，导致迁移失效）。
- `_app.tsx` 的 ErrorBoundary 已由 `app/error.tsx` 替代。
- `_document.tsx` 的主题脚本已迁入 `app/layout.tsx`。

**注意**：删除前用 `ls frontend/pages/` 确认只剩这 3 个文件（若有其他页面如 `pages/api/*.ts` 或 `pages/login.tsx` 等，需单独评估——本计划假设只有这 3 个核心文件）。

---

## 三、Sprint 7：集成测试 + 文档收尾

### S7.1 — 前端构建验证

**命令**（在 `frontend/` 目录）：
```powershell
npm install
npm run build
```

**验证点**：
- `npm install` 无 peer dependency 冲突（next 14.2 + react 18.3）。
- `npm run build` 成功完成，无 "Module not found" 错误。
- 构建产物 `.next/` 目录生成。
- 构建日志中根路由 `/` 显示为 `app/page.tsx`（而非 pages/index.tsx）。

**若失败的处理**：
- 若 `react-konva` 报 React 18.3 兼容性问题：保留 `react ^18.2.0`，仅升级 next。
- 若 `@monaco-editor/react` 报 ESM 转译错误：确认 `transpilePackages` 已包含。
- 若 TS 类型错误：`next.config.js` 已有 `ignoreBuildErrors: true`，不应阻塞。

### S7.2 — 根路由烟雾测试

**命令**：
```powershell
npm run dev
```

**验证点**（浏览器访问 `http://localhost:3000`）：
1. 页面加载无白屏、无控制台错误。
2. 主题正确应用（`<html data-theme="dark">` 在首次 paint 前设置，无 FOUC 闪烁）。
3. 登录表单正常显示（`AuthForm` 组件渲染）。
4. WebSocket 连接正常（登录后会话列表加载）。
5. 故意触发错误（如清空 localStorage token 后访问受限资源）→ `app/error.tsx` 错误页显示。

### S7.3 — 回归测试现有功能

**验证点**：
1. `/admin` 路由仍正常（已迁移的 App Router 页面不受影响）。
2. `/chat`、`/canvas`、`/charts` 路由正常。
3. `npm run test`（vitest）现有测试全部通过。

### S7.4 — 更新架构对比文档

**文件**：`架构对比分析与修改计划.md`（修改）

**改动**：在 P1.2 前端框架升级章节末尾追加完成标记：
```markdown
> **✅ 完成状态（2026-07-07）**：已升级至 Next.js 14.2.15 + React 18.3.1，
> 根路由迁移至 App Router（`app/page.tsx`），主题 FOUC 脚本迁入 `app/layout.tsx`，
> 错误边界改用原生 `app/error.tsx`，`transpilePackages` 声明 6 个 ESM 包。
> Pages Router 残留（index.tsx/_app.tsx/_document.tsx）已删除。
```

同时更新 P0.2 pluggy 章节的完成状态（若尚未标记）。

### S7.5 — 更新项目记忆

**文件**：项目 memory（`project_memory.md`）

**追加内容**：
- Sprint 5 (pluggy) + Sprint 6 (Next.js 14) 完成状态
- App Router 迁移的关键决策（14.2 而非 15、transpilePackages 清单、机械迁移策略）
- 前端构建的已知约束（PowerShell 下 `npm run build` 的注意事项）

---

## 四、假设与决策

### 4.1 关键假设

1. **`frontend/pages/` 仅含 3 个文件**：index.tsx、_app.tsx、_document.tsx（探索已确认，但删除前需 `ls` 二次确认）。
2. **`pages/index.tsx` 的相对路径在 `app/page.tsx` 中有效**：因 app/ 和 pages/ 同级（已确认）。
3. **现有 `app/` 子路由（admin/chat/canvas/charts）不受根路由迁移影响**：App Router 各路由独立。
4. **`react-konva@18.2.10` 兼容 React 18.3.1**：18.3 是 18.x 最后 minor，API 无破坏性变更。

### 4.2 关键决策

| 决策 | 选择 | 理由 |
|------|------|------|
| Next.js 版本 | 14.2.15（非 15.x） | 避免 React 19 破坏 konva/framer-motion |
| React 版本 | 18.3.1（非 19） | 与 react-konva 18.2.10 兼容 |
| 根路由迁移策略 | 机械复制 + `'use client'` 前缀 | 2870 行业务逻辑不重写，降低风险 |
| ErrorBoundary | 原生 `app/error.tsx` | App Router 无 `_app.tsx`，原生错误边界是官方方案 |
| 主题 FOUC 脚本位置 | `app/layout.tsx` 的 `<head>` 内联 | App Router 无 `_document.tsx`，必须内联 |
| transpilePackages 清单 | 6 个 ESM 包 | 基于 package.json 依赖分析 |
| Pages Router 残留处理 | S6.5 验证后删除 | 避免 App/Pages 路由冲突 |

---

## 五、验证步骤汇总

### 5.1 Sprint 6 完成标准

- [ ] `frontend/package.json` 的 next/react/react-dom/@next/bundle-analyzer 版本已升级
- [ ] `frontend/app/page.tsx` 存在，首行为 `'use client';`
- [ ] `frontend/app/error.tsx` 存在，导出 `RootError` 组件
- [ ] `frontend/app/layout.tsx` 包含主题 FOUC 脚本（在 reduced-motion 脚本之前）
- [ ] `frontend/next.config.js` 包含 `transpilePackages` 数组（6 个包）
- [ ] `frontend/pages/index.tsx`、`_app.tsx`、`_document.tsx` 已删除
- [ ] `npm run build` 成功

### 5.2 Sprint 7 完成标准

- [ ] `npm run dev` 启动无错误
- [ ] 浏览器访问 `http://localhost:3000` 正常渲染
- [ ] 主题无 FOUC 闪烁
- [ ] `/admin`、`/chat`、`/canvas`、`/charts` 路由正常
- [ ] `npm run test`（vitest）现有测试通过
- [ ] `架构对比分析与修改计划.md` 已更新完成状态
- [ ] 项目 memory 已更新

---

## 六、风险与回滚

### 6.1 主要风险

1. **`react-konva@18.2.10` 与 React 18.3.1 peer dependency 警告**：npm install 可能报 peer dep 警告但不阻塞（react-konva 声明 `peerDependencies: react@^18`）。
2. **`pages/index.tsx` 中可能有 Pages Router 专属 API**（如 `next/router` 的 `useRouter`）→ App Router 下应改用 `next/navigation`。若构建报错，需逐个替换。
3. **`app/layout.tsx` 的 `<head>` 标签在 App Router 中受限**：Next.js 14 推荐用 metadata API，但 `dangerouslySetInnerHTML` 脚本仍可在 `<head>` 内工作（已由 admin 路由验证）。

### 6.2 回滚策略

若 Sprint 6 构建失败且无法快速修复：
1. 恢复 `frontend/package.json` 版本（git checkout）
2. 删除 `frontend/app/page.tsx`、`app/error.tsx`
3. 恢复 `frontend/app/layout.tsx`（移除主题脚本）
4. 恢复 `frontend/next.config.js`（移除 transpilePackages）
5. `frontend/pages/` 的 3 个文件若已删除，从 git 恢复

---

## 七、任务清单

| ID | 任务 | 文件 | 状态 |
|----|------|------|------|
| S6.1 | 升级 package.json 依赖版本 | `frontend/package.json` | ⏳ |
| S6.2 | 创建 app/page.tsx（根路由迁移） | `frontend/app/page.tsx` | ⏳ |
| S6.3 | 创建 app/error.tsx（根错误边界） | `frontend/app/error.tsx` | ⏳ |
| S6.4 | 更新 app/layout.tsx（主题 FOUC 脚本） | `frontend/app/layout.tsx` | ⏳ |
| S6.5 | 更新 next.config.js（transpilePackages） | `frontend/next.config.js` | ⏳ |
| S6.6 | 删除 pages/ 残留 + 构建验证 | `frontend/pages/*` | ⏳ |
| S7.1 | 前端构建验证 | `frontend/` | ⏳ |
| S7.2 | 根路由烟雾测试 | 浏览器 | ⏳ |
| S7.3 | 回归测试现有功能 | `frontend/` | ⏳ |
| S7.4 | 更新架构对比文档 | `架构对比分析与修改计划.md` | ⏳ |
| S7.5 | 更新项目记忆 | memory | ⏳ |
