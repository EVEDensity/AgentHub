# AgentHub 插件开发指南

AgentHub 工具系统基于 [pluggy](https://pluggy.readthedocs.io/) 框架实现插件化。插件可以拦截工具调用、修改参数与结果、注册自定义工具，无需修改核心代码。

## 一、Hook 生命周期

工具执行的完整生命周期中，插件按以下顺序介入：

```
启动阶段
  └─ register_tools()     ← 插件声明自定义工具（仅启动时调用一次）
  └─ tool_categories()    ← 插件声明关心的工具类别（用于过滤）

每次工具调用
  ├─ pre_tool_use()       ← 执行前：可拦截 / 修改参数
  ├─ [工具执行]
  └─ post_tool_use()      ← 执行后：可修改结果 / 记录日志 / 脱敏
```

### 执行顺序

AgentHub 采用**双轨制**：

1. **pluggy 轨道**（同步）：内置插件 + 第三方插件，通过 `@hookimpl` 注册
2. **legacy 轨道**（异步）：通过 `hook_manager.register_pre()` 注册的 async hook

`run_pre_hooks` 先跑 pluggy（同步快速拦截），再跑 legacy async hooks。
`run_post_hooks` 先跑 legacy async hooks，再跑 pluggy（保证 sanitize 看到最终结果）。

## 二、Hook 规范（4 个 hookspec）

定义在 [app/services/tools/plugin_spec.py](../app/services/tools/plugin_spec.py)：

### 2.1 `pre_tool_use`

```python
@hookspec
def pre_tool_use(
    self,
    tool_name: str,
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any] | None:
    """工具执行前调用。"""
```

**返回值**：
- `None` — 继续执行
- `{"blocked": True, "reason": "..."}` — 拦截工具调用
- `{"modified_input": {...}}` — 替换工具输入参数

第一个返回 `blocked=True` 的插件会短路整个链。

**context 常见字段**：
- `tenant_id`、`user_id`、`session_id`、`agent_id` — 调用方身份
- `roles`（list[str]）、`scopes`（list[str]）— RBAC 身份信息
- `risk_level`（"low"|"normal"|"high"）— 工具风险等级
- `sanitize_level`（"off"|"basic"|"strict"）— 输出脱敏级别

### 2.2 `post_tool_use`

```python
@hookspec
def post_tool_use(
    self,
    tool_name: str,
    arguments: dict[str, Any],
    result: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any] | None:
    """工具执行后调用。"""
```

**返回值**：
- `None` — 不修改结果
- `{"modified_result": {...}}` — 合并到结果（多个插件的 `modified_result` 会叠加，后者覆盖前者）

### 2.3 `register_tools`

```python
@hookspec
def register_tools(self) -> list[dict[str, Any]] | None:
    """注册自定义工具。"""
```

返回工具定义列表，每个 dict 包含：`name`、`description`、`category`、`parameters`（JSON Schema）、`handler`（callable 或导入路径字符串）。仅启动时调用。

### 2.4 `tool_categories`

```python
@hookspec
def tool_categories(self) -> list[str] | None:
    """声明关心的工具类别（用于过滤）。"""
```

返回 `None` 表示接收所有工具的事件；返回 `["file", "web"]` 表示只接收这些类别。

## 三、编写第一个插件

最小插件（~20 行）：

```python
# plugins/my_plugin/plugin.py
from app.services.tools.plugin_spec import hookimpl

class MyPlugin:
    @hookimpl
    def pre_tool_use(self, tool_name, arguments, context):
        if tool_name == "code_execute":
            code = arguments.get("code", "")
            if "rm -rf" in code:
                return {"blocked": True, "reason": "危险命令已拦截"}
        return None

    @hookimpl
    def post_tool_use(self, tool_name, arguments, result, context):
        if result.get("success"):
            print(f"✓ {tool_name} 执行成功")
        return None
```

## 四、三种加载方式

### 4.1 内置插件

放入 `app/services/tools/plugins/` 目录，文件会被 `PluginManager.load_builtin_plugins()` 自动注册。适合随 AgentHub 发布的核心插件。

### 4.2 entry_point（第三方 pip 包）

在 `pyproject.toml` 或 `setup.py` 中声明：

```toml
# pyproject.toml
[project.entry-points."agenthub.plugins"]
my_plugin = "my_package.plugin:MyPlugin"
```

安装后，`PluginManager.load_entry_points()` 会自动发现并注册。适合分发到 PyPI 的第三方包。

### 4.3 路径加载（本地开发）

通过环境变量 `PLUGINS_PATH` 指定：

```bash
# 单个文件
PLUGINS_PATH=plugins/my_plugin/plugin.py python -m uvicorn app.main:app

# 整个目录（扫描所有 *.py）
PLUGINS_PATH=plugins/ python -m uvicorn app.main:app
```

插件类（带 `@hookimpl` 方法的类）会被实例化并注册为 `user.<ClassName>`。适合本地开发、企业自定义、临时插件。

## 五、内置插件

AgentHub 自带 3 个内置插件，在 [app/services/tools/plugins/](../app/services/tools/plugins/) 目录：

| 插件 | 注册名 | Hook | 行为 |
|---|---|---|---|
| AuditPlugin | `builtin.audit` | post_tool_use | 记录工具调用审计日志（tool_name、user、success、result 摘要） |
| PermissionPlugin | `builtin.permission` | pre_tool_use | RBAC scope 检查：高风险工具要求 `tool:execute:high` 或 agent_operator+ 角色 |
| SanitizePlugin | `builtin.sanitize` | post_tool_use | 输出脱敏（复用 OutputSanitizer，过滤 AWS key / JWT / 私钥等） |

### PermissionPlugin 风险矩阵

镜像 Go 侧 [iam/abac.go](../services/go/shared/iam/abac.go) 的 `BuiltinToolRisk`：

| 风险 | 工具示例 | 所需 scope |
|---|---|---|
| high | code_execute, file_write, shell, terminal | `tool:execute:high` 或 agent_operator+ 角色 |
| normal | web_search, http_request, file_read, memory_write | `tool:execute` 或 `tool:execute:medium` |
| low | memory_read, list_files, read_file | 始终允许 |

**dev mode 兼容**：当 `context` 无 `roles` 和 `scopes` 时（本地开发），PermissionPlugin 放行。它不替代 Go 侧 ABAC，而是 Python 侧的 defense-in-depth。

## 六、调试技巧

### 查看已加载插件

```python
from app.services.tools.plugin_manager import plugin_manager
plugin_manager.load_all()
print(plugin_manager.list_plugins())
# {'builtin.audit': 'AuditPlugin', 'builtin.permission': 'PermissionPlugin', ...}
```

### 调整日志级别

```python
import logging
logging.getLogger("agenthub.tools.plugins").setLevel(logging.DEBUG)
logging.getLogger("agenthub.plugins.audit").setLevel(logging.INFO)
```

### 手动触发 hook（测试）

```python
from app.services.tools.plugin_manager import plugin_manager
plugin_manager.load_all()

# 调用 pre_tool_use（返回结果列表）
results = plugin_manager.hook.pre_tool_use(
    tool_name="code_execute",
    arguments={"code": "print(1)"},
    context={"user_id": "u1", "roles": ["member"]},
)
print(results)  # [{'blocked': True, 'reason': "..."}] 或 [None]
```

### 检查插件是否注册

```python
plugin_manager.is_registered("builtin.audit")  # True
plugin_manager.is_registered("user.MyPlugin")   # 视是否加载而定
```

## 七、注意事项

1. **hookimpl 方法必须是同步的**：pluggy 的 hook 调用是同步的。如果插件需要异步操作（如 DB 写入），在 hookimpl 内部用 `asyncio.create_task` 或线程池调度，不要阻塞。
2. **不要在 hookimpl 中抛异常**：异常会被 HookManager 捕获并记录 warning，但不会阻断工具执行。若需拦截，返回 `{"blocked": True}`。
3. **modified_result 是合并而非替换**：多个 post_tool_use 插件返回的 `modified_result` 会 `update` 合并，后注册的覆盖先注册的。
4. **插件类需无参构造函数**：`__init__(self)` 不接受参数。配置通过环境变量或读取全局单例获取。
