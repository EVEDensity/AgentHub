# AgentHub 全面改进与迭代实施方案

> 编制日期：2026-07-07
> 基准文档：[架构对比分析与修改计划.md](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/架构对比分析与修改计划.md)
> 用户决策：① 全部 7 项一次性推进 ② 引入 pluggy 框架 ③ 升级 Next.js + 迁移 App Router
> 核心原则：复用已有实现、增量演进、保留双轨兼容、每 Sprint 可独立验收

---

## 一、Context（背景与动机）

用户开启 [架构对比分析与修改计划.md](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/架构对比分析与修改计划.md) 后要求"全面的改进和迭代"。该文档对照五层开源架构方案，列出 7 项改造（P0×3 + P1×2 + P2×2）。

源码核查后发现**原计划文档对现状有多处低估**，关键修正：

| 原计划描述 | 实际代码状态 | 影响 |
|---|---|---|
| 沙盒"subprocess，需新建容器沙盒" | [services/go/sandbox-service/](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/go/sandbox-service/cmd/sandbox-service/main.go) **已存在**完整 Go 沙盒服务（REST API + Docker 客户端 + seccomp/CapDrop/readonly rootfs/tmpfs/noop 降级） | P0.1 从"3 周新建"降为"1.5 周接线+集成" |
| 工具"17 个" | [definitions.py:938](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/tools/definitions.py#L938) BUILTIN_TOOLS 实际 **27 个工具** | 迁移基数更大 |
| 权限"仅 admin/developer 两角色" | [rbac.go](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/go/shared/iam/rbac.go) 已有 4 角色+16 scope；[session_guard.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/auth/session_guard.py) 已有 SessionRole；[tool_permission_rules](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/api/admin/tools.py#L162) 表已支持细粒度规则 | P0.3 主要是"接线+补角色矩阵" |
| vLLM"需新建 VLLMAdapter" | [main.py:465](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/python/model_adapter_service/main.py#L465) `OpenAICompatibleProvider` **已存在**且注释明确支持 vLLM | P1.1 工作量极小 |
| Nginx"需新建反向代理" | [k8s/base/ingress.yaml](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/k8s/base/ingress.yaml) 已用 `ingressClassName: nginx` + 完整注解 | P2.2 仅需补 Ingress Controller 部署清单 |

预期成果：在 4.5 周内完成全部 7 项改造，让架构对齐目标态，且不破坏现有 27 个工具、双轨切流、K8s 部署链路。

---

## 二、实施总表（7 Sprint，共 ~22.5 人日）

| Sprint | 任务 | 工作量 | 依赖 |
|---|---|---|---|
| S1 | P1.1 vLLM 别名+compose+文档 + P2.1 Milvus 注释 + P2.2 Nginx Ingress Controller | 1.5 人日 | 无（低风险先跑通） |
| S2 | P0.1-A sandbox-service 接线（Dockerfile + compose + Python 客户端） | 3 人日 | S1 |
| S3 | P0.1-B SandboxExecutor 抽象层 + 输出过滤 + 降级 | 4 人日 | S2 |
| S4 | P0.3 RBAC 增强（5 角色 + 工具风险矩阵 + workspace_acl） | 4 人日 | 可并行 S2/S3 |
| S5 | P0.2 工具插件化（**pluggy 重写 HookManager** + 27 工具迁移 + 热加载） | 6 人日 | S4 |
| S6 | P1.2 Next.js 14 升级 + **App Router 迁移** | 5 人日 | 可并行 S5 |
| S7 | 集成测试 + 文档更新 | 2 人日 | 全部 |

---

## 三、各项详细设计

### P0.1 沙盒执行隔离（Sprint 2-3，7 人日）

#### 现状
- [builtin_tools.py:1123-1325](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/tools/builtin_tools.py#L1123-L1325) `code_execute_handler` 用 `asyncio.create_subprocess_exec` 在 `.agenthub_exec/` 跑 Python/bash，**无容器隔离、无网络白名单、无资源配额、无输出过滤**
- [services/go/sandbox-service/](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/go/sandbox-service/cmd/sandbox-service/main.go) **已实现**：POST/GET/DELETE /containers、POST /containers/{id}/exec、/logs、/stats、/healthz；[internal/docker/client.go](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/go/sandbox-service/internal/docker/client.go) 已设置 SecurityOpt/CapDrop ALL/ReadonlyRootfs/Tmpfs/Memory/NanoCPUs/NetworkMode；DOCKER_SOCKET 不可达时自动 noop 降级
- **缺**：sandbox-service 无 Dockerfile、不在 compose、Python 端无 HTTP 客户端、无输出过滤器

#### 改造架构
```
Python app/ 端
  code_execute_handler → SandboxExecutor (抽象接口)
    ├─ SubprocessExecutor  (复用现有 _run_subprocess，降级路径)
    └─ RemoteSandboxExecutor (HTTP→sandbox-service:8097)
  → OutputSanitizer (敏感信息过滤)
                            ↓ HTTP
Go sandbox-service (复用现有)
  ├─ noop mode (开发环境)
  └─ docker mode (生产，挂载 /var/run/docker.sock)
     └─ Container: agenthub/sandbox:latest (seccomp+CapDrop+readonly+tmpfs)
```

#### 文件清单
**新建：**
- `app/services/sandbox/__init__.py`
- `app/services/sandbox/base.py` — `SandboxExecutor` 抽象接口 + `ExecutionResult` dataclass（字段：success/stdout/stderr/exit_code/duration_ms/error/metadata/backend）
- `app/services/sandbox/subprocess_executor.py` — **迁移** [builtin_tools.py:1259-1324](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/tools/builtin_tools.py#L1259-L1324) 的 `_run_subprocess`/`_build_python_cmd`/`_is_install_command`/`_is_one_liner`（不改逻辑）
- `app/services/sandbox/remote_executor.py` — httpx 异步客户端，POST /containers + POST /containers/{id}/exec + DELETE
- `app/services/sandbox/output_sanitizer.py` — 9 种敏感模式正则（内网 IP / 169.254.169.254 / Bearer/API key / JWT / Slack webhook / AWS AKIA / PRIVATE KEY / etc/passwd / DB DSN）
- `app/services/sandbox/factory.py` — env `SANDBOX_MODE=auto/subprocess/remote`，auto 优先 remote+降级 subprocess
- `app/services/sandbox/tests/test_output_sanitizer.py` — 覆盖 9 种模式
- `app/services/sandbox/tests/test_factory.py` — 覆盖 3 种模式 + 降级
- `services/go/sandbox-service/Dockerfile` — 仿 [iam-service/Dockerfile](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/go/iam-service) 模板（golang:1.22-alpine builder + apk add git ca-certificates + GOPROXY=https://goproxy.cn,direct + go mod tidy + alpine:3.20 runtime + docker-cli）
- `deploy/sandbox-image/Dockerfile` — 沙盒镜像（python:3.11-slim + bash + 常用包），用于 `agenthub/sandbox:latest`
- `deploy/sandbox-image/requirements.txt`

**修改：**
- [builtin_tools.py:1123-1325](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/tools/builtin_tools.py#L1123) — `code_execute_handler` 改为 `from app.services.sandbox import get_executor; executor = await get_executor(); result = await executor.execute(...)`，原 `_run_subprocess` 等迁出
- [deploy/docker-compose.platform.yml](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/deploy/docker-compose.platform.yml) — 追加 `sandbox-service` 服务块（volumes 挂载 /var/run/docker.sock:ro，端口 8097，env SANDBOX_DEFAULT_IMAGE/CPU/MEMORY）
- [app/config.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/config.py) — 新增 SANDBOX_MODE/SANDBOX_SERVICE_URL/SANDBOX_IMAGE/SANDBOX_CPU_LIMIT/SANDBOX_MEMORY_MB/SANDBOX_NETWORK_ALLOW/SANDBOX_OUTPUT_SANITIZE_LEVEL
- `.env.example` — 沙盒相关环境变量示例

#### 关键代码模式
```python
# app/services/sandbox/base.py
class SandboxExecutor:
    async def execute(self, code: str, language: Literal["python","bash"],
                      cwd: str, timeout: int, workspace_root: str,
                      env: dict[str,str] | None = None) -> ExecutionResult: ...
    async def health_check(self) -> bool: ...

# app/services/sandbox/factory.py
async def get_executor() -> SandboxExecutor:
    mode = os.getenv("SANDBOX_MODE", "auto")
    if mode == "subprocess": return SubprocessExecutor()
    if mode == "remote" or (mode == "auto" and os.getenv("SANDBOX_SERVICE_URL")):
        remote = RemoteSandboxExecutor(os.getenv("SANDBOX_SERVICE_URL"))
        if await remote.health_check(): return remote
        logger.warning("sandbox: remote unreachable, falling back to subprocess")
    return SubprocessExecutor()
```

#### 验收
- `docker compose -f deploy/docker-compose.platform.yml build sandbox-service` 成功
- `curl http://localhost:8097/healthz` 返回 `{"status":"ok","mode":"docker"|"noop"}`
- 单元测试 9 种敏感模式 + 3 种 factory 模式通过
- 烟雾测试：`code_execute` 工具执行 `print("hello")`，metadata.backend 字段显示后端类型
- 安全测试：remote 模式下 `print(open("/etc/passwd").read())` 被只读根文件系统拒绝
- 降级测试：停止 sandbox-service 后 code_execute 自动回退 subprocess + warning

---

### P0.2 工具系统插件化（Sprint 5，6 人日）

#### 现状
- [tool_registry.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/tool_registry.py) ToolRegistry 单例 + ToolDefinition dataclass（含 name/description/category/parameters/return_type/examples/risk_level/handler/is_concurrency_safe/requires_user_confirmation）
- [hooks.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/tools/hooks.py) 213 行自研 HookManager（PreToolUseResult/PostToolUseResult + 3 层作用域 global/category/per-tool）
- [builtin_hooks.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/tools/builtin_hooks.py) 177 行已注册 audit_log_hook、file_write_safety_hook、code_sandbox_hook
- [api/admin/tools.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/api/admin/tools.py) 已有工具 CRUD + agent_tool_bindings + tool_permission_rules API
- **缺**：pluggy 框架、插件元数据（version/author/dependencies）、热加载机制、插件管理 API

#### 改造方案：引入 pluggy 重写 HookManager

**为什么 pluggy**：用户明确选择。pluggy 是 pytest 同源框架，提供 hookspec/hookimpl 标准化契约、firstresult/tryfirst/trylast 等执行控制、命名空间隔离。

**迁移路径**（保留向后兼容）：
1. 新建 `app/services/tools/plugin_system.py` 定义 pluggy hookspecs
2. 现有 HookManager 包装为 pluggy PluginManager 的 facade（保留 `register_pre/register_post` API 不动，内部改用 pluggy）
3. 现有 3 个 builtin_hooks 改写为 `@hookimpl` 装饰器函数
4. 27 个工具按 category 重组为 12 个插件模块

#### 文件清单
**新建：**
- `app/services/tools/plugin_system.py` — pluggy PluginManager + hookspecs（pre_tool_use/post_tool_use/register_tool/list_tools）
- `app/services/tools/plugins/__init__.py` — 插件加载器入口
- `app/services/tools/plugins/base.py` — `ToolPlugin` + `PluginManifest` dataclass（name/version/author/description/category/min_risk_level/dependencies/enabled/load_order）
- `app/services/tools/plugins/loader.py` — 目录扫描 + importlib 动态加载 + 依赖排序
- `app/services/tools/plugins/watcher.py` — asyncio 定时扫描（30s mtime 对比，不引入 watchdog）
- `app/services/tools/plugins/builtins/` 下 12 个插件模块：
  - `search_plugin.py`（web_search）
  - `file_plugin.py`（file_read/write/write_batch/search/patch/edit/glob/mkdir，8 个）
  - `code_plugin.py`（code_execute, command_execute，2 个）
  - `memory_plugin.py`（memory_search/save，2 个）
  - `browser_plugin.py`（browser_navigate/screenshot/extract/click/type，5 个）
  - `http_plugin.py`（http_request）
  - `skill_plugin.py`（skill_list/skill_load）
  - `artifact_plugin.py`（artifact_list/read）
  - `conversation_plugin.py`（conversation_search）
  - `agent_plugin.py`（invoke_agent/invoke_agents_parallel）
  - `task_plugin.py`（task）
- `app/services/tools/plugins/tests/test_loader.py`
- `app/services/tools/plugins/tests/test_pluggy_integration.py`
- `app/api/admin/plugins.py` — GET /admin/plugins（list）+ POST /admin/plugins/{name}/reload + PUT /admin/plugins/{name}/enable

**修改：**
- [hooks.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/tools/hooks.py) — HookManager 内部改用 pluggy.PluginManager，对外 API（register_pre/register_post/run_pre_hooks/run_post_hooks）保持不变，确保 streaming_executor.py 无需改动
- [builtin_hooks.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/tools/builtin_hooks.py) — 3 个 hook 改写为 `@hookimpl` 装饰器，注册为 pluggy plugin
- [definitions.py:938-966](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/tools/definitions.py#L938) — BUILTIN_TOOLS 列表保留作为兜底（向后兼容）
- [tools/__init__.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/tools/__init__.py) — `register_builtin_tools()` 改为先调用 `plugin_loader.load_all()`，BUILTIN_TOOLS 兜底
- [app/main.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/main.py) — lifespan 中启动 PluginWatcher
- [app/db/init_db.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/db/init_db.py) — 追加 `tool_plugins` 表（name PK/version/author/description/enabled/loaded_at/manifest_json）
- [app/api/admin/__init__.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/api/admin/__init__.py) — 注册 plugins 路由
- `requirements.txt` — 新增 `pluggy>=1.5.0`

#### 关键代码模式
```python
# app/services/tools/plugin_system.py
import pluggy
from typing import Any, Optional

HOOK_NAMESPACE = "agenthub.tools"

hookspec = pluggy.HookspecMarker(HOOK_NAMESPACE)
hookimpl = pluggy.HookimplMarker(HOOK_NAMESPACE)

class ToolHooksSpec:
    @hookspec(firstresult=True)
    async def pre_tool_use(self, tool_name: str, arguments: dict,
                           context: dict) -> Optional["PreToolUseResult"]:
        """Return PreToolUseResult to block/modify; None to continue.
        firstresult: first non-None wins (tryfirst for ordering)."""

    @hookspec
    async def post_tool_use(self, tool_name: str, arguments: dict,
                            result: dict, context: dict) -> None:
        """Side-effect hook; modify result via mutable dict or return PostToolUseResult."""

    @hookspec
    def register_tools(self) -> list["ToolDefinition"]:
        """Return list of ToolDefinition provided by this plugin."""

pm = pluggy.PluginManager(HOOK_NAMESPACE)
pm.add_hookspecs(ToolHooksSpec)

# app/services/tools/hooks.py (rewritten as pluggy facade)
class HookManager:
    def __init__(self):
        self._pm = pm  # reuse global PluginManager
    async def register_pre(self, tool_name, hook):
        # Wrap as pluggy plugin with tryfirst if tool_name is per-tool
        self._pm.register(_PreHookPlugin(tool_name, hook))
    async def run_pre_hooks(self, tool_name, arguments, context):
        result = await self._pm.hook.pre_tool_use(
            tool_name=tool_name, arguments=arguments, context=context)
        return result or PreToolUseResult()  # firstresult returns None if all skip

# app/services/tools/plugins/builtins/code_plugin.py
from app.services.tools.plugin_system import hookimpl
from app.services.tools.plugins.base import ToolPlugin, PluginManifest
from app.services.tools.definitions import CODE_EXECUTE, COMMAND_EXECUTE

class CodePlugin:
    @hookimpl
    def register_tools(self):
        return [CODE_EXECUTE, COMMAND_EXECUTE]

PLUGIN = ToolPlugin(
    manifest=PluginManifest(name="code_plugin", version="1.0.0",
                            author="agenthub", category="code", min_risk_level="L3"),
    plugin_cls=CodePlugin,
)
```

#### 验收
- `pytest app/services/tools/plugins/tests/` 全部通过
- 启动后 `tool_registry.count() == 27`（与改造前一致）
- 烟雾测试：新增 `builtins/demo_plugin.py`，30s 内自动注册到 registry
- API 测试：`GET /admin/plugins` 返回 12 个插件；`POST /admin/plugins/code_plugin/reload` 触发重载
- 回归测试：27 个工具全部可调用；3 个 builtin_hooks（audit/safety/sandbox）仍生效
- 兼容测试：现有 `HookManager.register_pre/register_post` API 调用方无需改动

---

### P0.3 权限与角色体系增强（Sprint 4，4 人日）

#### 现状
- Go 端 [rbac.go](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/go/shared/iam/rbac.go) 已有 4 角色（super_admin/tenant_admin/member/viewer）+ 16 scope + Policy
- app/ 端 [auth_service.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/auth_service.py) 有 `require_admin`/`get_current_user`/`write_audit`
- [session_guard.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/auth/session_guard.py) 有 SessionRole（OWNER/MEMBER/VIEWER，会话级）
- [tool_permission_rules](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/api/admin/tools.py#L162) 表已支持 agent_id+tool_pattern+path_pattern+behavior+priority+enabled
- 工具 `risk_level` L1/L2/L3 已存在，但 [permission.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/tools/permission.py) 的 check() 仅在 L3+DEFAULT 模式触发 ASK，**无角色矩阵**
- **缺**：app/ 端用户角色细分、工具风险×角色矩阵、跨用户工作区访问控制

#### 改造方案
**A. app/ 端角色对齐 Go 端（5 角色）**

| app/ 端 role（新） | 中文 | 默认 scope（参考 [rbac.go:45-50](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/go/shared/iam/rbac.go#L45-L50)） |
|---|---|---|
| `super_admin` | 超级管理员 | `*` |
| `tenant_admin` | 租户管理员 | session:*+agent:*+tool:*+memory:*+doc:*+audit:read+tenant:manage |
| `editor` | 编辑者 | session:read+write+create+agent:dispatch+read+tool:execute+memory:*+doc:upload+read |
| `operator` | 操作者 | session:read+write+create+agent:dispatch+read+tool:execute+memory:read+write+doc:upload+read |
| `viewer` | 只读 | session:read+agent:read+doc:read+audit:read |

**B. 工具风险×角色矩阵**

| 角色 \ 风险 | L1 | L2 | L3 |
|---|---|---|---|
| super_admin | allow | allow | allow |
| tenant_admin | allow | allow | ask |
| editor | allow | ask | ask |
| operator | allow | ask | deny |
| viewer | allow | deny | deny |

实现位置：扩展 [permission.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/tools/permission.py) `PermissionManager.check()`，在现有 L3 检查之前插入角色矩阵查询。

#### 文件清单
**新建：**
- `app/services/auth/role_matrix.py` — 角色×风险等级查询（内存缓存+DB 加载）
- `app/services/auth/workspace_acl.py` — 跨用户工作区访问控制
- `app/api/admin/user_roles.py` — 用户角色管理 API（GET/PUT /admin/users/{id}/role）
- `app/services/auth/tests/test_role_matrix.py` — 覆盖 5×3=15 种组合

**修改：**
- [auth/service.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/auth/service.py) — `create_user` 默认 role 从 `developer` 改为 `operator`；新增 `require_role(minimum)` 函数；`require_admin` 改为 `require_role("tenant_admin")` 的别名（向后兼容）
- [tools/permission.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/tools/permission.py) — `ToolPermissionContext` 新增 `user_role` 字段；`check()` 在 Step 4 之前插入角色矩阵查询
- [tools/streaming_executor.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/tools/streaming_executor.py) — `ToolPermissionContext` 传入 `user_role`
- [workspace_context.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/services/workspace_context.py) — `resolve_workspace_path` 增加跨用户工作区访问检查（通过 workspace_acl）
- [db/init_db.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/db/init_db.py) — 追加 `tool_role_matrix`（role+risk_level+behavior，PK(role,risk_level)）+ `workspace_acl`（user_id+workspace_owner_id+access_level+granted_by+granted_at+expires_at）表 DDL + 种子数据
- [api/admin/__init__.py](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/app/api/admin/__init__.py) — 注册 user_roles 路由

#### 数据库迁移
```sql
-- 历史数据迁移：admin→super_admin, developer→editor
UPDATE users SET role='super_admin' WHERE role='admin';
UPDATE users SET role='editor' WHERE role='developer';

-- tool_role_matrix 种子数据
INSERT INTO tool_role_matrix VALUES
  ('super_admin','L1','allow'),('super_admin','L2','allow'),('super_admin','L3','allow'),
  ('tenant_admin','L1','allow'),('tenant_admin','L2','allow'),('tenant_admin','L3','ask'),
  ('editor','L1','allow'),('editor','L2','ask'),('editor','L3','ask'),
  ('operator','L1','allow'),('operator','L2','ask'),('operator','L3','deny'),
  ('viewer','L1','allow'),('viewer','L2','deny'),('viewer','L3','deny')
ON CONFLICT (role, risk_level) DO NOTHING;
```

#### 验收
- `pytest app/services/auth/tests/test_role_matrix.py` 覆盖 15 种组合
- 数据库迁移成功，历史用户 admin→super_admin、developer→editor
- API 测试：viewer 调 `code_execute`(L3) 返回 403；operator 调 L3 返回 deny；tenant_admin 调 L3 首次 ask 确认后 allow
- 工作区测试：用户 A 无 workspace_acl 授权时访问用户 B 工作区被拒
- 回归测试：现有 `require_admin` 装饰的 API 仍能被 super_admin/tenant_admin 访问

---

### P1.1 vLLM 本地推理支持（Sprint 1，0.5 人日）

#### 现状
- [main.py:465](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/python/model_adapter_service/main.py#L465) `OpenAICompatibleProvider` **已存在**，注释明确"points at any URL that speaks the OpenAI chat completions protocol (e.g. vLLM, Ollama, LiteLLM proxy)"
- [main.py:536](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/python/model_adapter_service/main.py#L536) `_providers["openai-compatible"] = OpenAICompatibleProvider()` 已注册
- [main.py:569-570](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/python/model_adapter_service/main.py#L569) 未知模型 fallback 到 openai-compatible（当 OPENAI_COMPATIBLE_BASE_URL 设置时）
- **缺**：显式 `vllm` provider 别名、vLLM 服务在 compose、配置文档

#### 文件清单
**修改：**
- [main.py:536](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/python/model_adapter_service/main.py#L536) — 追加 `_providers["vllm"] = OpenAICompatibleProvider()` 别名
- [main.py:540-571](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/services/python/model_adapter_service/main.py#L540) — `get_provider()` 增加 `vllm-*`/`vllm/*` 前缀路由分支
- `OpenAICompatibleProvider.__init__` — 优先读 `VLLM_BASE_URL`/`VLLM_API_KEY`，fallback 到 `OPENAI_COMPATIBLE_*`
- [deploy/docker-compose.platform.yml](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/deploy/docker-compose.platform.yml) — 追加 `vllm` 服务（profile=vllm 默认禁用，含 GPU+CPU 两种 profile，端口 8106）
- `.env.example` — vLLM 配置示例

**新建：**
- `docs/vllm-deployment.md` — GPU/CPU 部署、模型列表、性能调优

#### 关键代码模式
```python
# main.py 路由分支
if model.startswith("vllm-") or model.startswith("vllm/"):
    if os.getenv("VLLM_BASE_URL") or os.getenv("OPENAI_COMPATIBLE_BASE_URL"):
        return providers["vllm"]
    return providers["mock"]
```

```yaml
# docker-compose.platform.yml
vllm:
  profiles: ["vllm"]  # 默认不启动
  image: vllm/vllm-openai:latest
  environment:
    VLLM_MODEL: "${VLLM_MODEL:-Qwen/Qwen2.5-7B-Instruct}"
    HUGGING_FACE_HUB_TOKEN: "${HF_TOKEN:-}"
    VLLM_API_KEY: "${VLLM_API_KEY:-not-needed}"
  command: >
    --model ${VLLM_MODEL:-Qwen/Qwen2.5-7B-Instruct}
    --host 0.0.0.0 --port 8000
    --tensor-parallel-size 1 --max-model-len 8192
    --gpu-memory-utilization 0.9 --trust-remote-code
  ports: ["8106:8000"]
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
    interval: 30s
    timeout: 10s
    retries: 3
    start_period: 120s
```

#### 验收
- `docker compose --profile vllm config` 校验通过
- 单元测试：`get_provider("vllm-Qwen2.5-7B")` 返回 OpenAICompatibleProvider 实例
- 单元测试：未设置 VLLM_BASE_URL 时 fallback 到 mock
- 烟雾测试（需 GPU）：启动 vLLM，调用 /v1/chat/completions model=vllm-Qwen2.5-7B 返回正常响应

---

### P1.2 Next.js 升级 + App Router 迁移（Sprint 6，5 人日）

#### 现状
- [package.json](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/frontend/package.json) Next 13.5.7 + React 18.2.0
- [pages/_app.tsx](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/frontend/pages/_app.tsx) ErrorBoundary 包装（迁移到 App Router 需改写为 `app/layout.tsx` + `app/global-error.tsx`）
- [pages/index.tsx](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/frontend/pages/index.tsx) 唯一页面，使用 dynamic import ssr:false、大量 hooks、复杂状态
- [next.config.js](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/frontend/next.config.js) 已用 `remotePatterns: []`（14 兼容）、`swcMinify: true`（14 默认，可移除）
- vitest + jsdom 测试框架，5 个测试文件

#### 改造方案：升级 + 迁移 App Router
**升级路径**：Next 13.5.7→14.2.x（LTS）+ React 18.2.0→18.3.x（React 19 仍 RC，不升级）

**App Router 迁移**（用户明确选择）：
1. 新建 `frontend/app/` 目录
2. `app/layout.tsx` — 替代 `pages/_app.tsx`，保留 ErrorBoundary 逻辑
3. `app/page.tsx` — 替代 `pages/index.tsx`，使用 `'use client'` 指令（因 index.tsx 大量使用 hooks 和 dynamic import）
4. `app/global-error.tsx` — App Router 错误边界（替代 ErrorBoundary 类组件）
5. **保留 `pages/` 目录**作为兜底（Next.js 14 支持混合路由，优先 app/ 后才查 pages/），降低迁移风险

#### 文件清单
**新建：**
- `frontend/app/layout.tsx` — 根 layout，导入 `'../styles/globals.css'`，包含 ErrorBoundary 包装
- `frontend/app/page.tsx` — 首页（`'use client'` + 现有 index.tsx 内容迁移）
- `frontend/app/global-error.tsx` — 全局错误边界（must be client component）
- `frontend/app/error.tsx` — 路由级错误边界（可选）

**修改：**
- [package.json](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/frontend/package.json) — `next: ^13.5.7`→`^14.2.15`，`react/react-dom: ^18.2.0`→`^18.3.1`，`@next/bundle-analyzer: ^13.5.7`→`^14.2.15`
- [next.config.js](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/frontend/next.config.js) — 移除 `swcMinify: true`（14 默认）；保留 webpack splitChunks；保留 typescript.ignoreBuildErrors（单独 Sprint 修 TS）
- [pages/_app.tsx](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/frontend/pages/_app.tsx) — 保留作为兜底（混合路由模式）
- [pages/index.tsx](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/frontend/pages/index.tsx) — 保留作为兜底

**关键代码模式**
```tsx
// frontend/app/layout.tsx
import '../styles/globals.css';
import ErrorBoundary from '../components/shared/ErrorBoundary'; // 抽取自 _app.tsx

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <ErrorBoundary>{children}</ErrorBoundary>
      </body>
    </html>
  );
}

// frontend/app/page.tsx
'use client';
// 迁移自 pages/index.tsx，保持所有 hooks/dynamic import 不变
import HomeContent from '../components/home/HomeContent'; // 抽取大组件
export default function Page() { return <HomeContent />; }

// frontend/app/global-error.tsx
'use client';
export default function GlobalError({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <html>
      <body>
        <h2>Something went wrong!</h2>
        <pre>{error.message}</pre>
        <button onClick={() => reset()}>Try again</button>
      </body>
    </html>
  );
}
```

#### 验收
- `cd frontend && npm install` 无 critical peer dependency 警告
- `npm run build` 成功，bundle 大小变化 ±10% 以内
- `npm test` 5 个测试全部通过
- 烟雾测试：首页加载、Monaco Editor、Konva 画布、PDF 预览四个核心功能正常
- 烟雾测试：`API_BACKEND=go` 与 `API_BACKEND=legacy` 两种模式都正常
- App Router 验证：访问 `/` 走 `app/page.tsx`（验证方式：在 layout.tsx 加临时 console.log，浏览器开发者工具可见）

---

### P2.1 Milvus 评估（Sprint 1，0.5 人日）

#### 文件清单
**修改：**
- [deploy/docker-compose.platform.yml](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/deploy/docker-compose.platform.yml) — 在 `qdrant` 服务后追加 Milvus 注释块（profile=milvus 默认禁用，含 etcd+minio 依赖）

**新建：**
- `docs/vector-db-migration-checklist.md` — 迁移评估点（向量规模/查询延迟/索引类型/运维成本）

#### 关键代码模式
```yaml
# 默认全部注释，启用方式：docker compose --profile milvus up
# milvus-etcd:
#   profiles: ["milvus"]
#   image: quay.io/coreos/etcd:v3.5.5
#   ...
# milvus:
#   profiles: ["milvus"]
#   image: milvusdb/milvus:v2.4.0
#   command: ["milvus", "run", "standalone"]
#   ports: ["19530:19530", "9091:9091"]
```

#### 验收
- `docker compose --profile milvus config` 校验通过
- 默认 `docker compose up`（不带 profile）不启动 Milvus

---

### P2.2 Nginx 反向代理（Sprint 1，0.5 人日）

#### 现状
- [k8s/base/ingress.yaml](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/k8s/base/ingress.yaml) 已用 `ingressClassName: nginx` + 现有注解（proxy-body-size/proxy-read-timeout/websocket-services/upstream-hash-by/cert-manager）
- **缺**：Nginx Ingress Controller 部署清单（裸集群兜底）、生产级注解（rate-limit/cors/ssl-redirect/security headers）

#### 文件清单
**新建：**
- `k8s/base/nginx-ingress-controller.yaml` — Nginx Ingress Controller Deployment + Service（裸集群兜底，已装集群可忽略）
- `k8s/overlays/prod/ingress-patch.yaml` — 生产环境补充注解

**修改：**
- [k8s/base/ingress.yaml](file:///d:/Users/xyn/Desktop/agenthub/AgenthubV1.2/k8s/base/ingress.yaml) — 追加生产级注解：ssl-redirect、rate-limit-connections/rps/burst、cors-allow-origin/methods/headers、configuration-snippet（静态资源缓存+安全头）

#### 关键注解
```yaml
nginx.ingress.kubernetes.io/ssl-redirect: "true"
nginx.ingress.kubernetes.io/rate-limit-connections: "100"
nginx.ingress.kubernetes.io/rate-limit-requests-per-second: "50"
nginx.ingress.kubernetes.io/rate-limit-burst: "100"
nginx.ingress.kubernetes.io/enable-cors: "true"
nginx.ingress.kubernetes.io/cors-allow-origin: "https://*.agenthub.example.com"
nginx.ingress.kubernetes.io/configuration-snippet: |
  location ~* \.(?:js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2)$ {
    expires 1y;
    add_header Cache-Control "public, immutable";
  }
  add_header X-Frame-Options "SAMEORIGIN" always;
  add_header X-Content-Type-Options "nosniff" always;
```

#### 验收
- `kubectl apply -f k8s/base/ingress.yaml` 在测试集群成功
- `curl -I https://api.agenthub.example.com/healthz` 返回 200 + 安全头
- `curl -X OPTIONS https://api.agenthub.example.com/api/agent` 返回 CORS 头
- 限流测试：1000 req/s 压测触发 429

---

## 四、风险规避

| 风险 | 概率 | 影响 | 规避措施 |
|---|---|---|---|
| K8s 内挂载 docker.sock 的安全风险 | 高 | 高 | K8s 环境推荐 gVisor/Kata Containers；docker-compose 可接受；生产加 PodSecurityPolicy |
| RBAC 迁移导致历史用户角色失效 | 中 | 高 | `users_role_chk` 约束含旧值；迁移脚本 UPDATE 后再收紧；`require_admin` 保留为别名 |
| pluggy 重写 HookManager 破坏现有 3 个 builtin_hooks | 中 | 中 | HookManager 对外 API 不变；3 个 hook 改写为 `@hookimpl` 后单独测试 audit/safety/sandbox 行为 |
| 27 个工具插件化破坏注册顺序 | 中 | 中 | `load_order` 字段控制顺序；BUILTIN_TOOLS 兜底；改造前后 `registry.list_names()` 对比 |
| App Router 迁移导致 SSR/hydration 问题 | 中 | 中 | `app/page.tsx` 用 `'use client'`；保留 `pages/` 作为兜底（混合路由）；逐页验证 |
| Next.js 14 与 react-konva 18.2.10 不兼容 | 低 | 中 | 升级前 `npm ls react-konva` 检查 peer dep；必要时锁定版本 |
| vLLM 启动慢导致 healthcheck 失败 | 中 | 低 | `start_period: 120s`；`depends_on: condition: service_healthy` |
| 输出过滤器误杀合法输出 | 中 | 低 | `SANDBOX_OUTPUT_SANITIZE_LEVEL=off/basic/strict` 可配；被过滤字段标 `[REDACTED-*]` |

### 回滚策略
- 每个 Sprint 改造前打 git tag（如 `v3.1-pre-sandbox`），出问题回滚到上一 Sprint tag
- 数据库迁移用 Alembic downgrade：`alembic downgrade -1`
- App Router 迁移保留 `pages/` 目录，可随时通过删除 `app/` 回滚

### 兼容性保障
- **BUILTIN_TOOLS 保留**：插件化后 definitions.py:938 的列表不删除
- **require_admin 保留**：RBAC 后改为 `require_role("tenant_admin")` 别名
- **SubprocessExecutor 保留**：SandboxExecutor 默认降级路径
- **OpenAICompatibleProvider 保留**：vLLM 别名指向同一实现
- **pages/ 目录保留**：App Router 迁移后作为兜底

---

## 五、关键决策记录

| 决策 | 选择 | 理由 |
|---|---|---|
| 沙盒执行 | 复用 Go sandbox-service + Python 抽象层 | 已有 90% 实现，避免重复造轮子 |
| 工具插件化 | 引入 pluggy 重写 HookManager | 用户明确选择；pluggy 是 pytest 同源框架，标准化契约 |
| RBAC | app/ 端 5 角色与 Go 端语义对齐，不共享 Policy | 两端运行时隔离，对齐语义即可 |
| vLLM | 复用 OpenAICompatibleProvider + 别名 | 协议兼容，零新代码 |
| 前端 | 升级 Next.js 14 + 迁移 App Router | 用户明确选择；保留 pages/ 兜底降低风险 |
| 工作区访问控制 | 新建 workspace_acl 表 | 现有 per-user/session 隔离不支持协作场景 |

---

## 六、验证（端到端集成测试，Sprint 7）

执行以下全链路测试验证 7 项改造协同工作：

1. **角色矩阵 + 沙盒**：viewer 用户调 `code_execute`(L3) → 403；super_admin 调 → RemoteSandboxExecutor 执行 → 输出过滤生效
2. **插件化 + 工具**：在 `builtins/` 新增 `demo_plugin.py` → 30s 内 `GET /admin/plugins` 可见 → 调用新工具成功
3. **vLLM + model-adapter**：启动 vLLM（CPU 模式）→ 调 `/v1/chat/completions model=vllm-Qwen2.5-7B` → 返回响应
4. **前端 + App Router**：访问 `/` → 走 `app/page.tsx` → Monaco/Konva/PDF 正常 → `npm test` 通过
5. **Milvus + Nginx**：`docker compose --profile milvus config` 通过；`kubectl apply -f k8s/base/ingress.yaml` + curl 安全头验证
6. **回归测试**：现有 27 个工具全部可调用；3 个 builtin_hooks（audit/safety/sandbox）仍生效；`require_admin` API 仍可被 super_admin/tenant_admin 访问

每个 Sprint 完成后单独验收（见各项"验收"小节），Sprint 7 做端到端集成测试。
