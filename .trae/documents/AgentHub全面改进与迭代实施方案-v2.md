# AgentHub 全面改进与迭代实施方案 v2

> 基于上一会话的探索结果与用户 3 项关键决策修订。本计划为**决策完整**版本——执行者无需再做选择，按章节顺序推进即可。
>
> **用户决策（已确认）**：
> 1. 全部 7 项一次性推进（~22.5 人日）
> 2. 引入 pluggy 框架（不扩展自建 HookManager）
> 3. 升级 Next.js 版本 + 迁移到 App Router（不保留 Pages Router）

---

## 一、当前状态分析（Phase 1 探索结论）

### Sprint 1 已完成项（核对通过）

| 项 | 状态 | 证据 |
|---|---|---|
| P1.1 vLLM Provider | ✅ | [main.py:526-593](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/python/model_adapter_service/main.py#L526-L593) `VLLMProvider` + `_strip_prefix` |
| P1.1 vLLM 注册 | ✅ | [main.py:611](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/python/model_adapter_service/main.py#L611) `_providers["vllm"]` |
| P1.1 vLLM /v1/models | ✅ | [main.py:712-721](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/python/model_adapter_service/main.py#L712-L721) |
| P1.1 vLLM compose | ✅ | [docker-compose.platform.yml:440-482](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/deploy/docker-compose.platform.yml#L440-L482) profile=vllm, 8106:8000 |
| P2.1 Milvus 注释 | ✅ | [docker-compose.platform.yml:47-87](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/deploy/docker-compose.platform.yml#L47-L87) |
| P2.2 Ingress 注解 | ✅ | [ingress.yaml:19-43](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/k8s/base/ingress.yaml#L19-L43) rate-limit/cors/security-headers |
| P2.2 nginx-controller | ✅ | [nginx-ingress-controller.yaml](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/k8s/base/nginx-ingress-controller.yaml) |
| P2.2 prod ingress-patch | ❌ | **缺失**——需创建 |
| .env.example | ✅ | [.env.example:38-55](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/.env.example#L38-L55) VLLM_* + SANDBOX_* |
| docs/ | ✅ | vllm-deployment.md + vector-db-migration-checklist.md |

### 关键架构现状

**Frontend（重大发现）**：已存在 `frontend/app/` 目录（App Router 部分迁移），包含：
- `app/layout.tsx`、`app/page.tsx`
- `app/admin/`（layout.tsx + page.tsx + error.tsx + loading.tsx）
- `app/app/[botId]/page.tsx`、`app/canvas/page.tsx`、`app/chat/page.tsx`、`app/charts/page.tsx`

但 `frontend/pages/` 仍残留 `index.tsx`、`_app.tsx`、`_document.tsx`——**混合状态**。Next.js 13.5+ 支持共存，但 App Router 优先。Sprint 6 工作量从"完整迁移"降为"升级版本 + 清理残留 + 修复兼容性"。

**package.json**：`next ^13.5.7`、`react ^18.2.0`、`react-dom ^18.2.0`、`zustand ^5.0.14`、`tailwindcss ^3.4.3`、`typescript ~5.4.0`。

**HookManager**：[hooks.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/tools/hooks.py) 216 行，3 层 scope（global → category → per-tool），`PreToolUseResult`/`PostToolUseResult` dataclass 已定义。自建系统，需用 pluggy 重构。

**RBAC**：[rbac.go](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/go/shared/iam/rbac.go) 4 角色（super_admin/tenant_admin/member/viewer）+ 16 scope + `Policy` 结构。无风险等级、无 workspace_acl。

**requirements.txt**：无 pluggy 依赖。

**sandbox-service**：[services/go/sandbox-service/cmd/sandbox-service/main.go](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/go/sandbox-service/cmd/sandbox-service/main.go) 已存在 Go 沙盒服务，但缺 Dockerfile + compose 接线。Python 侧 `code_execute_handler` 直接用 subprocess，未走远程沙盒。

---

## 二、迭代实施计划（7 个 Sprint）

### Sprint 1 收尾（0.5 人日）— 完成 P2.2

**目标**：补全 prod overlay 的 ingress-patch，关闭 Sprint 1。

#### 1.1 创建 `k8s/overlays/prod/ingress-patch.yaml`

**为什么**：prod overlay 当前只 patch 了 host（`api.agenthub.com`），但 prod 需要更严格的 rate-limit 和更长的 timeout。base ingress 的注解是通用基线，prod 应叠加生产专属策略。

**怎么做**：使用 JSON6902 patch 在现有 ingress 上追加/覆盖注解：
- `nginx.ingress.kubernetes.io/rate-limit-connections: "200"`（prod 提高到 200）
- `nginx.ingress.kubernetes.io/rate-limit-requests-per-second: "100"`
- `nginx.ingress.kubernetes.io/proxy-read-timeout: "600"`（prod 长会话）
- `nginx.ingress.kubernetes.io/proxy-send-timeout: "600"`
- 追加 `nginx.ingress.kubernetes.io/cors-allow-origin: "https://app.agenthub.com"`（prod 限定具体域名）

#### 1.2 更新 `k8s/overlays/prod/kustomization.yaml`

将新 patch 文件加入 `patches` 列表（第二个 `patches:` 块，针对 Ingress）。

#### 1.3 验证

- `python -c "import yaml; yaml.safe_load(open('k8s/overlays/prod/ingress-patch.yaml'))"` 语法检查
- `python -c "import yaml; yaml.safe_load(open('k8s/overlays/prod/kustomization.yaml'))"` 语法检查
- 标记 Task #3 完成

---

### Sprint 2（3 人日）— P0.1-A：sandbox-service 接线

**目标**：让已有的 Go sandbox-service 可构建、可部署、可被 Python 侧调用。

#### 2.1 创建 `services/go/sandbox-service/Dockerfile`

**基于模板**：[iam-service/Dockerfile](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/go/iam-service) 的标准 Go builder 模板（遵守 project_memory 硬约束）。

```dockerfile
# syntax=docker/dockerfile:1.4
FROM golang:1.22-alpine AS builder
RUN apk add --no-cache git ca-certificates
ENV GOPROXY=https://goproxy.cn,direct
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod tidy
COPY . .
RUN CGO_ENABLED=0 go build -o /out/sandbox-service ./services/go/sandbox-service/cmd/sandbox-service

FROM alpine:3.20
RUN apk add --no-cache ca-certificates docker-cli
COPY --from=builder /out/sandbox-service /usr/local/bin/sandbox-service
EXPOSE 8097
ENTRYPOINT ["sandbox-service"]
```

**为什么 docker-cli**：sandbox-service 通过 docker CLI 创建隔离容器执行用户代码，运行时镜像需要 docker-cli 二进制。

#### 2.2 在 `deploy/docker-compose.platform.yml` 追加 sandbox-service 块

位置：GO ONLINE TIER 区块末尾（iam-service 之后）。

```yaml
sandbox-service:
  build:
    context: ..
    dockerfile: services/go/sandbox-service/Dockerfile
  environment:
    <<: *go-env
    SANDBOX_ADDR: ":8097"
    SANDBOX_IMAGE: "${SANDBOX_IMAGE:-agenthub/sandbox:latest}"
    SANDBOX_CPU_LIMIT: "${SANDBOX_CPU_LIMIT:-1.0}"
    SANDBOX_MEMORY_MB: "${SANDBOX_MEMORY_MB:-512}"
    SANDBOX_NETWORK_ALLOW: "${SANDBOX_NETWORK_ALLOW:-none}"
    SANDBOX_OUTPUT_SANITIZE_LEVEL: "${SANDBOX_OUTPUT_SANITIZE_LEVEL:-basic}"
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock:ro
  depends_on: [tempo]
  ports: ["8097:8097"]
```

**为什么 docker.sock:ro**：sandbox-service 需要调用宿主 Docker daemon 创建沙盒容器；`:ro` 防止容器内篡改 socket。

#### 2.3 创建 `deploy/sandbox-image/Dockerfile`

**为什么单独的镜像**：sandbox-service 执行用户代码时启动一个一次性容器，这个容器需要预装 Python + 常用库，且最小化攻击面。

```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl jq && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir numpy pandas httpx requests
WORKDIR /sandbox
RUN useradd -m -u 1000 sandbox && chown -R sandbox:sandbox /sandbox
USER sandbox
CMD ["sleep", "infinity"]
```

#### 2.4 创建 `deploy/sandbox-image/README.md`

说明如何构建沙盒镜像：`docker build -t agenthub/sandbox:latest -f deploy/sandbox-image/Dockerfile deploy/sandbox-image/`

#### 2.5 验证

- `docker build -f services/go/sandbox-service/Dockerfile .` 构建通过
- `docker compose -f deploy/docker-compose.platform.yml config` 校验 YAML
- `curl http://localhost:8097/healthz` 启动后健康检查通过

---

### Sprint 3（4 人日）— P0.1-B：SandboxExecutor 抽象层 + 输出过滤

**目标**：Python 侧 `code_execute_handler` 支持三种执行模式（subprocess/remote/auto），并对输出做安全过滤。

#### 3.1 创建 `app/services/tools/sandbox_executor.py`

**核心抽象**：

```python
from __future__ import annotations
import os, logging, asyncio, httpx, re, json
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger("agenthub.tools.sandbox")

SandboxMode = Literal["subprocess", "remote", "auto"]

@dataclass
class SandboxResult:
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    mode: str  # 实际使用的模式

class SandboxExecutor:
    """根据 SANDBOX_MODE 选择执行后端。
    - subprocess: 本地子进程（开发默认，无隔离）
    - remote: 调用 Go sandbox-service（HTTP）
    - auto: 优先 remote，失败降级 subprocess
    """
    def __init__(self) -> None:
        self.mode: SandboxMode = os.getenv("SANDBOX_MODE", "auto")
        self.service_url = os.getenv("SANDBOX_SERVICE_URL", "http://sandbox-service:8097")
        self.sanitize_level = os.getenv("SANDBOX_OUTPUT_SANITIZE_LEVEL", "basic")

    async def execute(self, code: str, language: str = "python",
                      timeout: float = 30.0) -> SandboxResult: ...
    async def _execute_subprocess(self, code: str, language: str, timeout: float) -> SandboxResult: ...
    async def _execute_remote(self, code: str, language: str, timeout: float) -> SandboxResult: ...

class OutputSanitizer:
    """过滤执行输出中的敏感信息。"""
    PATTERNS = {
        "aws_key": re.compile(r'AKIA[0-9A-Z]{16}'),
        "github_token": re.compile(r'gh[pousr]_[A-Za-z0-9]{36}'),
        "private_key": re.compile(r'-----BEGIN (RSA |EC |)PRIVATE KEY-----'),
        "jwt": re.compile(r'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'),
        "ip_v4": re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'),
    }
    def sanitize(self, text: str, level: str = "basic") -> str: ...
```

**关键设计**：
- `auto` 模式：先尝试 remote（带 2s 连接超时），失败则降级 subprocess 并记录 warning
- `OutputSanitizer` 三级：`off`（不过滤）/ `basic`（过滤密钥 token）/ `strict`（basic + IP 脱敏 + 路径脱敏）
- 远程调用：`POST {service_url}/v1/execute` body=`{code, language, timeout}`

#### 3.2 重构 `app/services/tools/builtin_tools.py` 的 `code_execute_handler`

**当前**：[builtin_tools.py:1123-1325](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/tools/builtin_tools.py#L1123-L1325) 直接调 `_run_subprocess`。

**改造**：
- 保留 `_build_python_cmd`、`_run_subprocess` 作为 subprocess 后端实现
- `code_execute_handler` 改为：实例化 `SandboxExecutor` → `execute(code)` → `OutputSanitizer.sanitize(result.stdout)` → 返回
- 单例 `sandbox_executor = SandboxExecutor()` 模块级初始化

#### 3.3 单元测试 `app/services/tools/test_sandbox_executor.py`

覆盖：
- subprocess 模式执行 `print("hello")` 成功
- remote 模式 mock httpx 返回成功
- auto 模式 remote 失败 → 降级 subprocess
- OutputSanitizer basic 级别过滤 AWS key / GitHub token
- OutputSanitizer strict 级别过滤 IP

#### 3.4 验证

- `pytest app/services/tools/test_sandbox_executor.py -v` 全绿
- `SANDBOX_MODE=remote python -c "..."` 集成测试（需 sandbox-service 运行）

---

### Sprint 4（4 人日）— P0.3：RBAC 增强

**目标**：5 角色 + 风险等级矩阵 + workspace_acl，实现细粒度 RBAC。

#### 4.1 修改 `services/go/shared/iam/rbac.go`

**新增角色**：
```go
const (
    RoleSuperAdmin   = "super_admin"
    RoleTenantAdmin  = "tenant_admin"
    RoleAgentOperator = "agent_operator"  // 新增：可执行高风险工具，不可管理成员
    RoleMember       = "member"
    RoleViewer       = "viewer"
)
```

**新增 scope**：
```go
const (
    // ... 现有 16 个 ...
    ScopeToolExecuteHigh   = "tool:execute:high"   // 高风险工具（code_execute, file_write）
    ScopeToolExecuteMedium = "tool:execute:medium" // 中风险工具（web_search, http_request）
    ScopeWorkspaceAdmin    = "workspace:admin"     // 工作空间管理
    ScopeWorkspaceRead     = "workspace:read"
    ScopeModelManage       = "model:manage"         // 模型配置管理
)
```

**新增 `DefaultRoleScopes`**：为 `RoleAgentOperator` 配置 scope 集合（含 high/medium tool execute，不含 tenant:manage/role:manage）。

**新增风险等级映射**：
```go
type ToolRiskLevel int
const (
    RiskLow ToolRiskLevel = iota  // L1：只读工具
    RiskMedium                    // L2：web_search, http_request
    RiskHigh                      // L3：code_execute, file_write, shell
)

var ToolRiskMatrix = map[string]ToolRiskLevel{
    "code_execute": RiskHigh,
    "file_write":   RiskHigh,
    "shell":        RiskHigh,
    "web_search":   RiskMedium,
    "http_request": RiskMedium,
    "file_read":    RiskLow,
    "memory_read":  RiskLow,
}

// RoleRiskAllowance 定义每个角色允许的最大风险等级
var RoleRiskAllowance = map[string]ToolRiskLevel{
    RoleSuperAdmin:    RiskHigh,
    RoleTenantAdmin:   RiskHigh,
    RoleAgentOperator: RiskHigh,
    RoleMember:        RiskMedium,  // member 只能执行中风险及以下
    RoleViewer:        RiskLow,     // viewer 只读
}
```

#### 4.2 创建 `services/go/shared/iam/workspace_acl.go`

```go
package iam

type WorkspaceACL struct {
    WorkspaceID string
    UserID      string
    Role        string  // workspace 内角色（可不同于 tenant 角色）
    Permissions []string
}

type WorkspacePolicy struct {
    acls map[string]map[string]WorkspaceACL  // workspaceID -> userID -> ACL
}

func (p *WorkspacePolicy) CanExecute(workspaceID, userID, toolName string) bool { ... }
```

#### 4.3 数据库迁移 `services/go/iam-service/migrations/XXX_workspace_acl.sql`

```sql
CREATE TABLE IF NOT EXISTS workspace_acl (
    id BIGSERIAL PRIMARY KEY,
    workspace_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    role VARCHAR(32) NOT NULL,
    permissions JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(workspace_id, user_id)
);
CREATE INDEX idx_workspace_acl_workspace ON workspace_acl(workspace_id);
```

#### 4.4 增强 `app/api/admin/roles.py`

新增端点：
- `GET /admin/roles/risk-matrix` — 返回工具风险等级矩阵
- `GET /admin/roles/{role}/allowed-tools` — 返回角色允许的工具列表
- `PUT /admin/workspaces/{wid}/acl/{uid}` — 设置 workspace ACL

#### 4.5 单元测试 `services/go/shared/iam/rbac_test.go`

覆盖：5 角色 scope 展开、风险矩阵查询、workspace_acl 权限判定。

#### 4.6 验证

- `go test ./services/go/shared/iam/...` 全绿
- `go vet ./services/go/...` 通过
- 新端点 curl 烟雾测试

---

### Sprint 5（6 人日）— P0.2：工具插件化 pluggy

**目标**：引入 pluggy 框架，重构 HookManager，支持外部插件加载。

#### 5.1 添加依赖

**`requirements.txt`** 追加：
```
pluggy==1.5.0
```

#### 5.2 创建 `app/services/tools/plugin_spec.py`（hookspec）

```python
import pluggy
from typing import Any

HOOK_NAMESPACE = "agenthub"

hookspec = pluggy.HookspecMarker(HOOK_NAMESPACE)
hookimpl = pluggy.HookimplMarker(HOOK_NAMESPACE)

class ToolHookSpecs:
    """AgentHub 工具系统 hook 规范。"""

    @hookspec
    def pre_tool_use(self, tool_name: str, arguments: dict[str, Any],
                     context: dict[str, Any]) -> dict[str, Any] | None:
        """工具执行前调用。返回 {'blocked': bool, 'reason': str, 'modified_input': dict} 或 None。"""

    @hookspec
    def post_tool_use(self, tool_name: str, arguments: dict[str, Any],
                      result: dict[str, Any], context: dict[str, Any]) -> dict[str, Any] | None:
        """工具执行后调用。返回 {'modified_result': dict} 或 None。"""

    @hookspec
    def register_tools(self) -> list[dict[str, Any]]:
        """插件注册自定义工具。返回 ToolDefinition dict 列表。"""

    @hookspec
    def tool_categories(self) -> list[str]:
        """声明插件关心的工具类别（用于过滤）。"""
```

#### 5.3 创建 `app/services/tools/plugin_manager.py`

```python
import pluggy, importlib.metadata, logging
from .plugin_spec import hookspec, ToolHookSpecs

logger = logging.getLogger("agenthub.tools.plugins")

class PluginManager:
    def __init__(self) -> None:
        self.pm = pluggy.PluginManager("agenthub")
        self.pm.add_hookspecs(ToolHookSpecs)

    def load_builtin_plugins(self) -> None:
        """加载内置插件（audit, permission, sanitize）。"""
        from .plugins.builtin_audit import AuditPlugin
        from .plugins.builtin_permission import PermissionPlugin
        from .plugins.builtin_sanitize import SanitizePlugin
        self.pm.register(AuditPlugin(), name="builtin.audit")
        self.pm.register(PermissionPlugin(), name="builtin.permission")
        self.pm.register(SanitizePlugin(), name="builtin.sanitize")

    def load_entry_points(self) -> int:
        """通过 entry_point group 'agenthub.plugins' 加载第三方插件。"""
        return self.pm.load_setuptools_entrypoints("agenthub.plugins")

    def load_from_path(self, path: str) -> None:
        """从指定路径加载 Python 文件作为插件。"""
        import importlib.util, sys
        spec = importlib.util.spec_from_file_location("user_plugin", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # 查找模块中实现了 hookimpl 的类
        for name in dir(mod):
            obj = getattr(mod, name)
            if hasattr(obj, '_pluggy_hooks') and name != 'ToolHookSpecs':
                self.pm.register(obj(), name=f"user.{name}")

    @property
    def hook(self):
        return self.pm.hook
```

#### 5.4 创建内置插件 `app/services/tools/plugins/`

- `builtin_audit.py` — `AuditPlugin`：post_tool_use 记录审计日志
- `builtin_permission.py` — `PermissionPlugin`：pre_tool_use 检查 RBAC scope
- `builtin_sanitize.py` — `SanitizePlugin`：post_tool_use 过滤输出敏感信息（复用 OutputSanitizer）
- `__init__.py`

#### 5.5 适配 `app/services/tools/hooks.py`（向后兼容层）

**保留 `HookManager` 类**作为兼容层，内部委托给 `PluginManager`：
- `register_pre(tool_name, hook)` → 包装为 `@hookimpl` 注册到 PluginManager
- `run_pre_hooks(...)` → 调用 `plugin_manager.hook.pre_tool_use(...)`
- 现有调用方无需改动

#### 5.6 创建 `plugins/` 目录（项目根）

```
plugins/
├── README.md          # 插件开发指南
├── example_plugin/
│   ├── __init__.py
│   ├── plugin.py      # 示例：统计工具调用次数
│   └── setup.py       # entry_point 配置示例
└── pyproject.toml     # 可选：plugins 作为独立包
```

#### 5.7 创建 `docs/plugin-development.md`

涵盖：hookspec 说明、hookimpl 编写、entry_point 注册、路径加载、内置插件列表。

#### 5.8 验证

- `pytest app/services/tools/test_plugin_manager.py -v`
- 集成测试：加载 example_plugin 后 `hook.pre_tool_use` 被调用
- 向后兼容：现有 `hook_manager.register_pre(...)` 仍工作

---

### Sprint 6（3 人日，从 5 降为 3）— P1.2：Next.js 14 升级 + App Router 收尾

**目标**：升级到 Next.js 14，清理 pages/ 残留，修复 App Router 兼容性问题。

**工作量降低原因**：探索发现 `frontend/app/` 已存在并包含主要路由，无需从零迁移。

#### 6.1 升级 `frontend/package.json` 依赖

```json
{
  "dependencies": {
    "next": "^14.2.15",
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
    // 其他依赖保持不变
  }
}
```

**为什么 14.2.x 而非 15**：Next.js 15 要求 React 19，会破坏大量第三方依赖（konva、react-konva、@monaco-editor/react 等）。14.2 是 React 18 的最后一个主线版本，稳定且兼容。

#### 6.2 清理 `frontend/pages/` 残留

**删除**：
- `frontend/pages/_app.tsx` — App Router 用 `app/layout.tsx` 替代
- `frontend/pages/_document.tsx` — App Router 自动处理
- `frontend/pages/index.tsx` — 已被 `app/page.tsx` 替代

**前置检查**：先确认 `app/layout.tsx` 包含原 `_app.tsx` 的所有 Provider 包装（ZUSTAND_STORE_PROVIDER 等）。若缺失则先迁移。

#### 6.3 修复 App Router 兼容性

**常见问题**：
1. `"use client"` 指令：使用 hooks（useState/useEffect）的组件需在文件顶部加 `"use client"`
2. `next/navigation` 替代 `next/router`：`useRouter` from `next/navigation`
3. `next/link` 行为变化：14 不再需要 `<a>` 子元素
4. metadata API：`app/layout.tsx` 用 `export const metadata` 替代 `_document.tsx` 的 `<Head>`
5. `getServerSideProps` → Server Component 直接 await（若有）

**逐文件检查** `frontend/app/` 下所有 `.tsx`：
- `app/layout.tsx` — 确认 Provider 包装完整
- `app/page.tsx` — 确认 `"use client"`（若用 hooks）
- `app/admin/layout.tsx`、`app/admin/page.tsx` — 同上
- `app/chat/page.tsx`、`app/canvas/page.tsx`、`app/charts/page.tsx`、`app/app/[botId]/page.tsx`

#### 6.4 更新 `frontend/next.config.js`

```javascript
/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // App Router 下 transpilePackages 替代 experimental.transpilePackages
  transpilePackages: ['@monaco-editor/react', 'react-konva', 'konva'],
  // 保留现有 rewrites
  async rewrites() { /* ... 现有内容 ... */ },
};
module.exports = nextConfig;
```

#### 6.5 验证

- `cd frontend && npm install` 成功
- `npm run build` 成功（无 TypeScript 错误）
- `npm run dev` 启动，访问 `/`、`/admin`、`/chat`、`/canvas` 正常渲染
- `npm test` 现有测试通过

---

### Sprint 7（2 人日）— 集成测试 + 文档更新

#### 7.1 端到端集成测试

**测试矩阵**：

| 场景 | 验证点 |
|---|---|
| vLLM 推理 | `curl /v1/models` 含 vllm 模型；`curl /v1/chat/completions -d '{"model":"vllm-Qwen/..."}'` 返回结果 |
| Sandbox 远程执行 | `SANDBOX_MODE=remote` 调用 code_execute，sandbox-service 日志显示容器创建 |
| Sandbox 降级 | 停 sandbox-service，`SANDBOX_MODE=auto` 降级 subprocess + warning |
| RBAC 风险矩阵 | member 角色调用 code_execute → 拒绝；agent_operator → 允许 |
| pluggy 插件加载 | 启动日志显示 3 个内置插件注册；example_plugin 加载成功 |
| Next.js 14 | frontend build 成功；所有路由可访问 |
| Nginx Ingress | `kubectl apply -k k8s/overlays/prod` 注解正确 |

#### 7.2 更新 `重构进度清单与改进方案.md`

记录 7 个 Sprint 完成情况，更新 Phase 4 完成度。

#### 7.3 创建 `docs/architecture-v2.md`

汇总本次迭代的架构变更：vLLM 接入、Sandbox 隔离、pluggy 插件、RBAC 5 角色、Next.js 14 App Router。

---

## 三、假设与决策

### 假设
1. Go sandbox-service 现有 API（`/v1/execute` 端点）已可用——Sprint 2 仅需 Dockerfile + 接线，不改 Go 代码逻辑
2. `frontend/app/` 下的现有路由是可工作的（探索未发现明显错误）——Sprint 6 主要是版本升级而非重写
3. 现有 `hook_manager` 调用方（tool_registry、builtin_tools 等）通过兼容层无需改动

### 决策
1. **pluggy 而非扩展 HookManager**：用户明确决策。pluggy 提供 entry_point 加载、hookimpl 标记、规范化的插件发现机制，比自建 dict 注册更可扩展
2. **Next.js 14.2 而非 15**：避免 React 19 破坏第三方依赖
3. **sandbox 镜像独立构建**：与 sandbox-service 解耦，便于单独更新沙盒环境
4. **RBAC 新增 agent_operator 角色**：填补 member（不可执行高风险）和 tenant_admin（全权）之间的空档
5. **HookManager 保留为兼容层**：避免大规模改动现有调用方，pluggy 逐步替代

---

## 四、验证步骤总览

| Sprint | 验证命令/方式 |
|---|---|
| S1 收尾 | YAML 语法检查 + kustomize build |
| S2 | `docker build` + `docker compose config` + `/healthz` |
| S3 | `pytest test_sandbox_executor.py` + 集成测试 |
| S4 | `go test ./.../iam/` + `go vet` + curl 烟雾测试 |
| S5 | `pytest test_plugin_manager.py` + 向后兼容测试 |
| S6 | `npm install` + `npm run build` + `npm run dev` 路由验证 |
| S7 | 端到端集成测试矩阵 |

---

## 五、执行顺序与依赖

```
S1 收尾 ──► S2 (sandbox 接线) ──► S3 (SandboxExecutor)
                                              │
S4 (RBAC) ──► S5 (pluggy，复用 S4 的 risk matrix)
                                              │
                                    S6 (Next.js，独立)
                                              │
                                              ▼
                                    S7 (集成测试，依赖前 6 个)
```

**并行机会**：S4（RBAC，Go）与 S2/S3（Sandbox，Python+Go）可并行；S6（前端）与 S5（pluggy）可并行。

---

## 六、风险与缓解

| 风险 | 缓解 |
|---|---|
| Next.js 14 升级破坏 konva/react-konva | transpilePackages + 锁定 react@18.3 |
| pluggy 重构破坏现有 hook 调用 | HookManager 兼容层 + 向后兼容测试 |
| sandbox-service Docker 构建失败（go.work 依赖） | 遵守 project_memory：go mod tidy + GOPROXY |
| RBAC 新角色影响现有 JWT 签发 | DefaultRoleScopes 向后兼容 + 现有角色 scope 不变 |
| frontend/app 现有路由有隐藏 bug | S6 先 `npm run build` 暴露问题再修复 |
