# AgentHub 全面改进与迭代实施方案 v3（续作）

> 本计划是 v2 方案的**续作**，聚焦于尚未完成的 Sprint 5 收尾、Sprint 6、Sprint 7。
> v2 方案中 Sprint 1–4 已完成，Sprint 5 已完成约 30%（requirements.txt + plugin_spec.py）。
> 本计划为**决策完整**版本——执行者无需再做选择，按章节顺序推进即可。

---

## 一、当前状态核查（Phase 1 探索结论）

### 已完成（v2 Sprint 1–4）

| Sprint | 内容 | 状态 | 证据 |
|---|---|---|---|
| S1 收尾 | Nginx Ingress prod overlay | ✅ | [ingress-patch.yaml](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/k8s/overlays/prod/ingress-patch.yaml) + kustomization 合并 |
| S2 | sandbox-service Dockerfile + compose + sandbox-image | ✅ | [sandbox-service/Dockerfile](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/go/sandbox-service/Dockerfile) + compose 块 + [deploy/sandbox-image/](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/deploy/sandbox-image/) |
| S3 | SandboxExecutor + OutputSanitizer + /v1/execute | ✅ | [sandbox_executor.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/tools/sandbox_executor.py) + sandbox-service main.go /v1/execute 路由 + test_sandbox_executor.py |
| S4 | RBAC 5 角色 + workspace_acl + migration + admin API | ✅ | [rbac.go](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/go/shared/iam/rbac.go) + [workspace_acl.go](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/go/shared/iam/workspace_acl.go) + [014_workspace_acl_enhancement.sql](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/go/shared/db/migrations/014_workspace_acl_enhancement.sql) + [rbac.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/api/admin/rbac.py) + rbac_test.go（27 测试通过） |

### Sprint 5 已完成部分

| 项 | 状态 | 证据 |
|---|---|---|
| requirements.txt 添加 pluggy==1.5.0 | ✅ | [requirements.txt:20](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/requirements.txt#L20) |
| plugin_spec.py（4 个 hookspec） | ✅ | [plugin_spec.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/tools/plugin_spec.py)（pre_tool_use / post_tool_use / register_tools / tool_categories） |

### Sprint 5 待完成项（本计划覆盖）

| 项 | 状态 |
|---|---|
| plugin_manager.py（pluggy.PluginManager 封装） | ❌ 缺失 |
| plugins/ 目录（builtin_audit / builtin_permission / builtin_sanitize / __init__） | ❌ 缺失 |
| hooks.py 适配为向后兼容层 | ❌ 仍是原始 HookManager |
| 项目根 plugins/ 目录（example_plugin + README） | ❌ 缺失 |
| docs/plugin-development.md | ❌ 缺失 |
| test_plugin_manager.py | ❌ 缺失 |

### Sprint 6 关键发现（v2 计划需修正）

**v2 假设**："frontend/app/ 已存在并包含主要路由，Sprint 6 主要是版本升级 + 清理残留。"

**实际核查结果**：
- `frontend/app/` 确实存在，包含：`layout.tsx`、`admin/`（layout+page+error+loading）、`app/[botId]/page.tsx`、`canvas/page.tsx`、`chat/page.tsx`、`charts/page.tsx`
- **但 `frontend/app/page.tsx` 不存在！** 根路由 `/` 仍由 Pages Router 的 [pages/index.tsx](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/frontend/pages/index.tsx) 提供——这是 2870 行的 `AgentHubIM` 主聊天界面组件
- `frontend/pages/` 残留 3 个文件：`index.tsx`（主应用，**不能直接删**）、`_app.tsx`（含 ErrorBoundary）、`_document.tsx`（含主题 FOUC 阻断脚本）

**结论**：Sprint 6 不能简单"删除残留"。必须先将根路由迁移到 App Router，再清理 pages/。

**关键有利因素**：`app/` 和 `pages/` 都在 `frontend/` 下一级，二者到 `components/`、`lib/`、`types`、`hooks/` 的相对路径完全相同（都是 `../components`、`../lib` 等）。因此把 `pages/index.tsx` 的内容搬到 `app/page.tsx` **无需修改任何相对路径**，只需在文件顶部加 `'use client'` 指令。

### Sprint 7 待完成项

全部待完成：端到端集成测试 + 文档更新。

---

## 二、Sprint 5 收尾（约 3 人日）— P0.2：工具插件化 pluggy

**目标**：完成 pluggy 插件系统，支持内置插件 + 第三方插件加载 + 向后兼容现有 HookManager。

### 5.1 创建 `app/services/tools/plugin_manager.py`

**为什么**：pluggy 的 `PluginManager` 需要一个封装层，提供内置插件加载、entry_point 发现、路径加载三种注册方式，并对调用方暴露统一的 `hook` 属性。

**怎么做**：基于 [plugin_spec.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/tools/plugin_spec.py) 的 `ToolHookSpecs` 和 `HOOK_NAMESPACE="agenthub"`，封装如下：

```python
from __future__ import annotations
import logging, importlib.util, sys
import pluggy
from .plugin_spec import ToolHookSpecs

logger = logging.getLogger("agenthub.tools.plugins")

class PluginManager:
    """Wraps pluggy.PluginManager for the AgentHub tool system.

    Loading order (call load_all() at startup):
      1. builtin plugins (audit, permission, sanitize)
      2. entry_point group 'agenthub.plugins' (third-party packages)
      3. PLUGINS_PATH env var (single .py file or directory)
    """
    def __init__(self) -> None:
        self.pm = pluggy.PluginManager("agenthub")
        self.pm.add_hookspecs(ToolHookSpecs)

    def load_builtin_plugins(self) -> None:
        from .plugins.builtin_audit import AuditPlugin
        from .plugins.builtin_permission import PermissionPlugin
        from .plugins.builtin_sanitize import SanitizePlugin
        self.pm.register(AuditPlugin(), name="builtin.audit")
        self.pm.register(PermissionPlugin(), name="builtin.permission")
        self.pm.register(SanitizePlugin(), name="builtin.sanitize")

    def load_entry_points(self) -> int:
        return self.pm.load_setuptools_entrypoints("agenthub.plugins")

    def load_from_path(self, path: str) -> None:
        # 支持单文件或目录；目录则扫描所有 *.py
        # 用 importlib.util.spec_from_file_location 加载
        # 扫描模块中带 _pluggy_hooks 属性的类，实例化后注册
        ...

    def load_all(self) -> None:
        """启动时调用：builtin → entry_points → PLUGINS_PATH"""
        self.load_builtin_plugins()
        try:
            n = self.load_entry_points()
            if n: logger.info("plugin_manager: loaded %d entry_point plugins", n)
        except Exception as exc:
            logger.warning("plugin_manager: entry_point load failed: %s", exc)
        env_path = os.getenv("PLUGINS_PATH")
        if env_path:
            try: self.load_from_path(env_path)
            except Exception as exc:
                logger.warning("plugin_manager: path load failed: %s", exc)

    @property
    def hook(self):
        return self.pm.hook

    def list_plugins(self) -> dict[str, str]:
        """返回 {name: plugin_class_name} 供 admin API 查询。"""
        ...

plugin_manager = PluginManager()  # 模块级单例
```

### 5.2 创建内置插件 `app/services/tools/plugins/`

**目录结构**：
```
app/services/tools/plugins/
├── __init__.py
├── builtin_audit.py       # AuditPlugin: post_tool_use → 审计日志
├── builtin_permission.py  # PermissionPlugin: pre_tool_use → RBAC scope 检查
└── builtin_sanitize.py    # SanitizePlugin: post_tool_use → 输出脱敏
```

**`__init__.py`**：导出三个插件类 + `__all__`。

**`builtin_audit.py`** — AuditPlugin：
- `@hookimpl` 标注 `post_tool_use`，记录工具调用审计日志（tool_name、user_id、success、duration）
- 复用现有 `app.services.audit_service` 或 `write_audit`（若存在）；否则用 `logging` 记录
- `tool_categories()` 返回 `None`（关心所有工具）

**`builtin_permission.py`** — PermissionPlugin：
- `@hookimpl` 标注 `pre_tool_use`，根据 context 中的 `roles` / `scopes` 检查工具权限
- 复用 Sprint 4 的 RBAC 风险矩阵概念：高风险工具（code_execute/file_write/shell）要求 `tool:execute:high` scope 或 agent_operator+ 角色
- 中风险（web_search/http_request）要求 `tool:execute:medium`
- 返回 `{"blocked": True, "reason": "insufficient permission"}` 或 None
- **注意**：此插件是可选增强，不替代现有 ABAC；若 context 缺少 roles/scopes 字段则放行（dev mode 兼容）

**`builtin_sanitize.py`** — SanitizePlugin：
- `@hookimpl` 标注 `post_tool_use`，对 `result` 中的 stdout/output 字段做脱敏
- 复用 [sandbox_executor.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/tools/sandbox_executor.py) 的 `OutputSanitizer`（import 单例或类）
- 脱敏级别从 `context.get("sanitize_level", "basic")` 读取
- 返回 `{"modified_result": {...result, "stdout": sanitized}}`

### 5.3 适配 `app/services/tools/hooks.py` 为向后兼容层

**为什么**：现有调用方（tool_registry、builtin_tools、streaming_executor 等）依赖 `hook_manager.register_pre(...)` / `run_pre_hooks(...)`。直接替换会引发大规模改动。保留 `HookManager` 类作为兼容层，内部委托给 `PluginManager`。

**怎么做**：修改 [hooks.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/tools/hooks.py)：
- 保留 `PreToolUseResult` / `PostToolUseResult` dataclass（不变）
- 保留 `HookManager` 类的方法签名（`register_pre` / `register_post` / `run_pre_hooks` / `run_post_hooks` / `get_hook_count`）
- **内部委托**：
  - `__init__` 中持有 `self._pm = plugin_manager`（import 单例），调用 `self._pm.load_all()` 惰性初始化
  - `register_pre(tool_name, hook)`：把 async hook 包装成 `@hookimpl` 风格的同步方法注册到 pluggy（用 `tryfirst=True` 保证注册顺序），或直接注册为 pluggy 插件实例
  - `run_pre_hooks(...)`：调用 `self._pm.hook.pre_tool_use.pre_tool_use(...)` 拼装结果为 `PreToolUseResult`
  - 由于 pluggy hook 是同步的而现有 hooks 是 async，**包装策略**：在 run_pre_hooks 内用 `asyncio.run` 或在 hookimpl 内部 schedule async 任务——**更简单的做法**是让 pluggy hook 直接处理同步逻辑，async 包装层在 HookManager.run_pre_hooks 里统一 await
  - **关键**：pluggy 的 hookspec 返回 dict 或 None，HookManager 把 dict 结果映射回 dataclass
- 模块级单例 `hook_manager = HookManager()` 保持不变，调用方零改动

**简化方案**（推荐）：由于现有 async hook 与 pluggy 同步模型有阻抗，采用**双轨制**：
- pluggy 管理内置插件 + 第三方插件（同步 hookimpl）
- HookManager 保留原有 `_pre_hooks` / `_post_hooks` dict 管理用户通过 `register_pre` 注册的 async hook
- `run_pre_hooks` 先跑 pluggy 的 `hook.pre_tool_use`（同步，快速拦截），再跑原有 async hooks
- 这样向后兼容 100%，pluggy 作为"额外"插件层叠加

### 5.4 创建项目根 `plugins/` 目录

```
plugins/
├── README.md              # 插件开发快速指南
└── example_plugin/
    ├── __init__.py
    └── plugin.py          # ExamplePlugin: 统计工具调用次数（post_tool_use 累加 counter）
```

**`plugins/example_plugin/plugin.py`**：
```python
from app.services.tools.plugin_spec import hookimpl

class ExamplePlugin:
    """Example plugin: counts tool invocations (for demo/testing)."""
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    @hookimpl
    def post_tool_use(self, tool_name, arguments, result, context):
        self.counts[tool_name] = self.counts.get(tool_name, 0) + 1
        return None  # 不修改结果
```

**`plugins/README.md`**：简述目录用途、如何用 `PLUGINS_PATH=plugins/example_plugin/plugin.py` 加载、指向 `docs/plugin-development.md`。

### 5.5 创建 `docs/plugin-development.md`

涵盖：
1. Hook 生命周期图（register_tools → tool_categories → pre_tool_use → [执行] → post_tool_use）
2. 4 个 hookspec 的签名与返回值契约（引用 plugin_spec.py）
3. 编写第一个插件（最小示例，~20 行）
4. 三种加载方式：
   - 内置（放 `app/services/tools/plugins/`）
   - entry_point（setup.py / pyproject.toml 配置 `agenthub.plugins` group）
   - 路径（`PLUGINS_PATH` 环境变量）
5. 内置插件列表与行为
6. 调试技巧（`plugin_manager.list_plugins()`、日志级别）

### 5.6 创建 `app/services/tools/test_plugin_manager.py`

测试覆盖：
1. `PluginManager` 初始化 + hookspec 注册
2. `load_builtin_plugins()` 后 `list_plugins()` 返回 3 个内置插件
3. 注册一个测试插件 → `hook.pre_tool_use` 被调用并返回预期 dict
4. `pre_tool_use` 返回 `{"blocked": True}` 时短路
5. `post_tool_use` 修改结果（`modified_result`）
6. `register_tools` 返回工具定义列表
7. `load_from_path` 从临时 .py 文件加载插件
8. **向后兼容测试**：`hook_manager.register_pre("web_search", async_hook)` 后 `run_pre_hooks("web_search", ...)` 仍能触发原 async hook
9. **集成测试**：加载 example_plugin 后 `post_tool_use` 调用计数器递增

### 5.7 验证

- `cd d:\Users\xyn\Desktop\agenthub\AgenthubV1.2 && python -m pytest app/services/tools/test_plugin_manager.py -v` 全绿
- `python -c "from app.services.tools.plugin_manager import plugin_manager; plugin_manager.load_builtin_plugins(); print(plugin_manager.list_plugins())"` 输出 3 个内置插件
- 向后兼容：现有 `from app.services.tools.hooks import hook_manager` + `hook_manager.register_pre(...)` 仍工作

---

## 三、Sprint 6（约 3 人日）— P1.2：Next.js 14 升级 + App Router 根路由迁移

**目标**：升级 Next.js 到 14.2.x，将根路由 `/` 迁移到 App Router，清理 pages/ 残留，修复兼容性。

**工作量修正**：v2 估计 3 人日，但发现 `app/page.tsx` 缺失，需增加根路由迁移。仍按 3 人日（迁移是机械操作，相对路径无需改动）。

### 6.1 升级 `frontend/package.json` 依赖

**修改**：
```json
{
  "dependencies": {
    "next": "^14.2.15",      // 从 ^13.5.7 升级
    "react": "^18.3.1",      // 从 ^18.2.0 升级
    "react-dom": "^18.3.1",  // 从 ^18.2.0 升级
    "@next/bundle-analyzer": "^14.2.15"  // 从 ^13.5.7 升级，保持与 next 主版本一致
  }
}
```

**为什么 14.2.x 而非 15**：Next.js 15 要求 React 19，会破坏 konva / react-konva / @monaco-editor/react / framer-motion 等第三方依赖。14.2 是 React 18 的最后一个主线版本，稳定且兼容。

**其他依赖保持不变**（zustand ^5.0.14、tailwindcss ^3.4.3、typescript ~5.4.0、vitest ^4.1.9 等均兼容 Next 14）。

### 6.2 迁移根路由：创建 `frontend/app/page.tsx`

**为什么**：当前 `/` 由 [pages/index.tsx](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/frontend/pages/index.tsx)（2870 行 `AgentHubIM` 组件）提供。App Router 下 `app/page.tsx` 优先级高于 `pages/index.tsx`，需先创建 App Router 版本。

**怎么做**（机械迁移，无需改相对路径）：
1. 读取 `pages/index.tsx` 全部内容
2. 创建 `app/page.tsx`，内容 = `'use client';\n\n` + pages/index.tsx 原始内容（去掉 `export default function AgentHubIM` 之前的注释行保留）
3. **关键**：`'use client'` 指令必须在文件第一行（App Router 强制要求，因为组件用了 useState/useEffect/WebSocket 等 client API）
4. 相对路径 `../components`、`../lib`、`../types`、`../hooks` 无需修改（app/ 和 pages/ 同级）

**验证迁移正确性**：迁移后 `npm run build` 应无新增错误（注意 next.config.js 已有 `typescript: { ignoreBuildErrors: true }`，会隐藏 TS 错误，需单独跑 `tsc --noEmit` 检查）。

### 6.3 迁移 ErrorBoundary：创建 `frontend/app/error.tsx`

**为什么**：[pages/_app.tsx](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/frontend/pages/_app.tsx) 包含一个 class 组件 `ErrorBoundary`。App Router 用 `app/error.tsx` 原生支持错误边界。

**怎么做**：创建 `app/error.tsx`（'use client' 必需）：
```tsx
'use client';
import { useEffect } from 'react';

export default function Error({
  error, reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => { console.error('[AppRouter Error]', error); }, [error]);
  return (
    <div style={{ padding: 40, fontFamily: 'monospace', background: '#121418', minHeight: '100vh', color: '#F87272' }}>
      <h1 style={{ fontSize: 24, marginBottom: 16, color: '#E4E7EC' }}>客户端错误</h1>
      <div style={{ background: '#191C22', border: '1px solid #F87272', padding: 20, marginBottom: 16 }}>
        <strong>消息：</strong> {error.message}
      </div>
      <pre style={{ background: '#191C22', color: '#E4E7EC', padding: 16, borderRadius: 6, overflow: 'auto', maxHeight: 400, fontSize: 12 }}>
        {error.stack}
      </pre>
      <button onClick={reset} style={{ marginTop: 16, padding: '8px 16px', background: '#3b82f6', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer' }}>
        重试
      </button>
    </div>
  );
}
```

### 6.4 迁移主题 FOUC 脚本到 `frontend/app/layout.tsx`

**为什么**：[pages/_document.tsx](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/frontend/pages/_document.tsx) 有一个阻塞脚本，在首绘前从 localStorage 读取主题并应用到 `<html data-theme>`。App Router 无 `_document.tsx`，需将此脚本移入 [app/layout.tsx](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/frontend/app/layout.tsx) 的 `<head>`。

**怎么做**：在 `app/layout.tsx` 现有的 `<head>` 中（已有 reduced-motion 脚本和字体 link），追加主题脚本：
```tsx
<script dangerouslySetInnerHTML={{ __html: `
  (function() {
    try {
      var t = localStorage.getItem('agenthub_theme') || localStorage.getItem('agenthub_theme_legacy');
      if (t === 'dark' || t === 'light' || t === 'warm') {
        document.documentElement.setAttribute('data-theme', t);
        document.documentElement.style.colorScheme = t === 'dark' ? 'dark' : 'light';
      }
    } catch(e) {}
  })();
` }} />
```

### 6.5 更新 `frontend/next.config.js`

**修改**：
- 保留现有 `reactStrictMode`、`swcMinify`、`poweredByHeader`、`compress`、`typescript.ignoreBuildErrors`、`images`、`rewrites`、`webpack` 配置
- **新增** `transpilePackages`（App Router 14 推荐，替代 experimental.transpilePackages，确保 @monaco-editor/react / react-konva / konva 等 ESM 包正确转译）：
```javascript
transpilePackages: ['@monaco-editor/react', 'react-konva', 'konva', 'framer-motion'],
```
- **移除** `swcMinify: true`（Next 14 默认启用，显式设置会触发 deprecation warning）—— 可选，保留也无害

### 6.6 删除 `frontend/pages/` 残留文件

**前置条件**：6.2–6.5 全部完成且 `npm run build` 通过。

**删除**：
- `frontend/pages/index.tsx`（内容已迁移到 app/page.tsx）
- `frontend/pages/_app.tsx`（ErrorBoundary 已迁移到 app/error.tsx，globals.css 已在 app/layout.tsx import）
- `frontend/pages/_document.tsx`（主题脚本已迁移到 app/layout.tsx）
- 若 `frontend/pages/` 目录变空则删除目录本身

### 6.7 验证

1. `cd frontend && npm install`（安装 next@14.2.15 + react@18.3.1）
2. `npm run build` 成功（关注是否有 App Router 特有错误）
3. `npm run dev` 启动，访问以下路由确认渲染正常：
   - `/`（主聊天界面，AgentHubIM）
   - `/admin`（管理面板）
   - `/chat`、`/canvas`、`/charts`
4. 主题切换正常（dark/light/warm 无 FOUC 闪烁）
5. 故意触发一个客户端错误，确认 `app/error.tsx` 兜底渲染
6. `npm test`（vitest）现有测试通过

---

## 四、Sprint 7（约 2 人日）— 集成测试 + 文档更新

### 7.1 端到端集成测试矩阵

| 场景 | 验证点 | 前置条件 |
|---|---|---|
| vLLM 推理 | `curl /v1/models` 含 vllm 模型；`curl /v1/chat/completions -d '{"model":"vllm-..."}'` 返回结果 | vLLM 服务启动（profile=vllm） |
| Sandbox 远程执行 | `SANDBOX_MODE=remote` 调用 code_execute，sandbox-service 日志显示容器创建 | sandbox-service + sandbox 镜像就绪 |
| Sandbox 降级 | 停 sandbox-service，`SANDBOX_MODE=auto` 降级 subprocess + warning | — |
| Sandbox 输出过滤 | 执行输出含 AWS key/JWT → 被 `[REDACTED:...]` 替换 | `SANDBOX_OUTPUT_SANITIZE_LEVEL=basic` |
| RBAC 风险矩阵 | member 角色调用 code_execute → PermissionPlugin 拦截；agent_operator → 放行 | Sprint 4 + 5 完成 |
| pluggy 插件加载 | 启动日志显示 3 个内置插件注册；`PLUGINS_PATH=plugins/example_plugin/plugin.py` 后 example_plugin 加载 | Sprint 5 完成 |
| pluggy 向后兼容 | 现有 `hook_manager.register_pre(...)` 注册的 async hook 仍触发 | Sprint 5 完成 |
| Next.js 14 路由 | `/`、`/admin`、`/chat`、`/canvas`、`/charts` 全部可访问 | Sprint 6 完成 |
| Next.js 主题 | 切换 dark/light/warm 无 FOUC | Sprint 6 完成 |
| Nginx Ingress | `kubectl apply -k k8s/overlays/prod` 注解正确（rate-limit/cors/timeout） | K8s 集群 |

### 7.2 更新 `重构进度清单与改进方案.md`

记录 7 个 Sprint 完成情况，更新 Phase 4 完成度（预计从当前 ~70% 提升到 ~95%）。

### 7.3 创建 `docs/architecture-v2.md`

汇总本次迭代的架构变更：
1. **vLLM 接入**（S1）— 本地推理可选部署，profile=vllm
2. **Sandbox 隔离**（S2+S3）— Go sandbox-service + Docker 容器 + OutputSanitizer 三级过滤
3. **RBAC 5 角色**（S4）— 新增 agent_operator，workspace_acl 细粒度控制
4. **pluggy 插件系统**（S5）— 4 hookspec + 3 内置插件 + entry_point/路径加载
5. **Next.js 14 App Router**（S6）— 根路由迁移 + ErrorBoundary + 主题 FOUC
6. **Nginx Ingress 增强**（S1 收尾）— prod overlay rate-limit/cors/timeout

### 7.4 验证

- 集成测试矩阵全部通过（或标注 skip 原因，如 vLLM 需 GPU）
- 文档更新完成
- `重构进度清单与改进方案.md` 反映最终状态

---

## 五、假设与决策

### 假设
1. Sprint 1–4 的产出（sandbox-service、SandboxExecutor、RBAC、workspace_acl）已通过各自的单元测试，可作为 Sprint 5/7 的基础
2. `frontend/app/` 下现有路由（admin/chat/canvas/charts/app/[botId]）在 Next 13.5 下已可工作，升级到 14.2 不会引入破坏性变更（14.x 向后兼容 13.x App Router）
3. `pages/index.tsx` 的相对路径 `../components`、`../lib` 等在迁移到 `app/page.tsx` 后仍然有效（app/ 与 pages/ 同级）
4. 现有 `hook_manager` 调用方未使用 `unregister_pre` / `unregister_post`（兼容层可简化）

### 决策
1. **pluggy + HookManager 双轨制**（Sprint 5.3）：pluggy 管理内置/第三方插件（同步 hookimpl），HookManager 保留原有 async hook dict。`run_pre_hooks` 先跑 pluggy 再跑 async。100% 向后兼容，避免 async/sync 阻抗失配。
2. **根路由机械迁移**（Sprint 6.2）：直接把 `pages/index.tsx` 内容搬到 `app/page.tsx` + `'use client'`，不重写组件。相对路径不变，风险最低。
3. **Next.js 14.2 而非 15**：避免 React 19 破坏 konva/react-konva/framer-motion 等依赖。
4. **transpilePackages 显式声明**（Sprint 6.5）：确保 @monaco-editor/react / react-konva / konva 在 App Router 下正确转译。
5. **ErrorBoundary 用 app/error.tsx**（Sprint 6.3）：App Router 原生错误边界，替代 _app.tsx 的 class ErrorBoundary。
6. **主题脚本内联到 layout.tsx**（Sprint 6.4）：App Router 无 _document.tsx，脚本放 layout.tsx 的 `<head>` 等效。

---

## 六、验证步骤总览

| Sprint | 验证命令/方式 |
|---|---|
| S5 收尾 | `pytest app/services/tools/test_plugin_manager.py -v` + `python -c "from app.services.tools.plugin_manager import plugin_manager; plugin_manager.load_builtin_plugins(); print(plugin_manager.list_plugins())"` + 向后兼容测试 |
| S6 | `cd frontend && npm install && npm run build` + `npm run dev` 路由验证 + 主题切换 + error.tsx 触发 |
| S7 | 集成测试矩阵 + 文档审查 |

---

## 七、执行顺序与依赖

```
S5 收尾（pluggy 插件系统）
   │
   ├── 5.1 plugin_manager.py
   ├── 5.2 plugins/（builtin_audit/permission/sanitize）
   ├── 5.3 hooks.py 兼容层（依赖 5.1）
   ├── 5.4 项目根 plugins/（独立）
   ├── 5.5 docs/plugin-development.md（独立）
   └── 5.6 test_plugin_manager.py（依赖 5.1-5.4）
          │
          ▼
S6（Next.js 14，独立于 S5，可并行）
   ├── 6.1 package.json 升级
   ├── 6.2 app/page.tsx 迁移
   ├── 6.3 app/error.tsx
   ├── 6.4 layout.tsx 主题脚本
   ├── 6.5 next.config.js
   └── 6.6 删除 pages/（依赖 6.2-6.5 全部完成）
          │
          ▼
S7（集成测试，依赖 S5 + S6）
```

**并行机会**：S5（Python pluggy）与 S6（前端）完全独立，可并行推进。

---

## 八、风险与缓解

| 风险 | 缓解 |
|---|---|
| pluggy 同步 hook 与现有 async HookManager 阻抗失配 | 双轨制：pluggy 跑同步快速拦截，async hook 保留原 dict；run_pre_hooks 先同步后异步 |
| `pages/index.tsx` 迁移后 `'use client'` 未覆盖所有子组件 | App Router 下子组件自动继承客户端边界；且 next.config.js 已有 `ignoreBuildErrors: true` 兜底 |
| Next 14 升级破坏 konva/react-konva | transpilePackages 显式声明 + 锁定 react@18.3.1 |
| 内置 PermissionPlugin 误拦截现有工具调用 | dev mode（context 无 roles/scopes）放行；仅在 context 含明确 roles 时检查 |
| app/page.tsx 迁移后相对路径失效 | 已核查：app/ 与 pages/ 同级，`../components` 等路径不变 |
| Sprint 7 集成测试需多服务启动 | 标注前置条件，无 GPU 的测试（vLLM）标注 skip |
