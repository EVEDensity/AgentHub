# CLI 鲁棒性重构与边界说明

> 状态：accepted（执行基线，能力按下表逐项验收）  
> 版本：2026-09-05  
> 适用范围：`app/cli/`、Harness/Runner、Mission Control HTTP/SSE 客户端及 CLI 契约测试。

本文是后续 AI 开发 CLI 的约束文档。代码存在不等于任务完成；只有实现、契约测试和必要的真实环境证据同时具备，才能将状态标记为 completed。

## 当前状态

| 阶段 | 任务 | 状态 | 证据或剩余工作 |
|---|---|---|---|
| 第一阶段 | 修复 `on_text_delta` 在 Planner/Reflective 重试中丢失 | completed | 使用 `dataclasses.replace()`；Harness/CLI 测试通过 |
| 第一阶段 | SSE 独立解析器 | completed | `app/cli/sse.py`；覆盖 id、event、多行 data、心跳、retry |
| 第一阶段 | stream/tools 逻辑彻底分离 | partial | 已取消 Harness 强制关闭 tools；不支持流式工具的 Adapter 回退 completion；统一 `ModelRequest` 仍待后续 |
| 第一阶段 | 工具状态使用 `call_id` | partial | Reducer 已按 `call_id` 关联；旧事件使用兼容 ID，生产事件仍需强制携带 call_id |
| 第一阶段 | 稳定错误分类 | partial | 新增 `CliErrorKind` 与分类器；Transport/API 尚未全部迁移到统一错误层 |
| 第二阶段 | 独立 HTTP transport | partial | 已新增 `app/cli/transport.py` 并接入认证和 GET 重试；旧客户端方法仍待全部迁移 |
| 第二阶段 | 独立 SSE client | completed | `app/cli/sse_client.py` 负责连接生命周期、帧解析和连接状态事件；旧客户端保留兼容代理 |
| 第二阶段 | Mission/Decision/Artifact API 拆分 | partial | 已新增 API 门面，旧客户端仍作为兼容实现 |
| 第二阶段 | 每个 API 的契约测试 | partial | Transport 和 API 门面已有契约测试，完整 HTTP 边界矩阵仍待补齐 |
| 第二阶段 | 统一重试、超时、认证 | partial | Transport 已统一基础认证/GET 重试；SSE、写请求和错误映射仍待迁移 |
| 第三阶段 | 可靠性测试矩阵 | partial | 已覆盖 parser、重复/乱序事件、同名工具 call_id 并发、GET 503 重试及 TTY 40/80/120；真实 TTY、断线期间 Decision、provider 和 registry 验收仍缺 |

## 不可突破的边界

```text
HTTP Transport
  -> SSE Frame Parser
  -> Event Normalizer
  -> EventReducer
  -> Session Snapshot
  -> Rich / TUI / REPL / JSONL renderer
```

- Transport 只负责连接、认证、超时、重试和响应解码，不推断 Mission 状态。
- SSE parser 只负责标准 SSE 分帧，不访问 Mission、Decision 或 Renderer。
- EventNormalizer 负责版本兼容和事件名称映射，不执行副作用。
- EventReducer 是 CLI 展示状态的唯一折叠点；Renderer 不得自行维护业务状态。
- Harness 负责模型循环、工具调用、预算和 checkpoint；不得直接写 Mission 真相。
- Runner 负责租约、隔离执行、Artifact 收集和提交 VERIFYING；不得绕过 Decision。
- Mission Control 是 Mission、WorkUnit、Artifact、Evidence、Decision、Outcome 的唯一持久化真相。
- 普通对话必须使用只读模型路径，不创建 Attempt 快照，不写文件，不执行命令。

## 第一阶段实施规则

### 统一模型请求

后续不得通过“是否有 callback”决定是否启用工具。目标接口为：

```python
@dataclass(frozen=True)
class ModelRequest:
    messages: tuple[Message, ...]
    tools: tuple[ToolSchema, ...] = ()
    stream: bool = False
    tool_choice: str = "auto"
    timeout: float = 60.0
```

`stream=True` 和 `tools` 是两个独立维度。Adapter 负责把统一请求转换为供应商格式；不支持流式工具调用的供应商必须返回结构化能力错误，不能静默假装成功。

### 工具状态

所有工具事件必须携带稳定 `call_id`。Reducer 的键必须是 `call_id`，工具名只用于显示。缺少 `call_id` 的旧事件只能进入 diagnostics，不得与同名调用合并。

### 错误分类

至少统一以下错误：

```text
AuthError          认证失败，不重试
TransportError     连接、读取和 DNS 错误，可按策略重试
TimeoutError       请求或 Mission 超时
ProtocolError      SSE/JSON/事件契约错误
ProviderError      provider 返回的结构化错误
ConflictError      Decision、租约或恢复冲突
```

UI、JSONL、CI 的差异只能发生在错误投影层，不能由各层重新猜测错误类型。

## 第二阶段拆分目标

目标目录：

```text
app/cli/transport/http.py
app/cli/transport/sse.py
app/cli/control/missions.py
app/cli/control/decisions.py
app/cli/control/artifacts.py
app/cli/control/permissions.py
```

所有 API 共享同一个 Transport 实例。认证 token、连接池、默认 timeout、指数退避和 request-id 由 Transport 管理。API 模块不得直接创建新的 `httpx.Client`，不得各自实现重试。

## 第三阶段可靠性矩阵

必须有自动化测试，且测试名称明确标注层级：

- `contract`: SSE 空帧、多行帧、心跳、id/event/retry。
- `state`: 重复事件、乱序事件、同名工具并发、Decision 生命周期。
- `integration`: SSE 断线恢复、断线期间 Decision、进程启动失败。
- `provider`: 无效工具参数、超时、429、5xx、真实 provider tool-call。
- `terminal`: TTY 宽度 40/80/120、spinner、换行和无 TTY 降级。
- `release`: registry 安装、升级、回滚和稳定退出码。

没有真实 provider、真实 TTY 或真实 registry 证据时，只能标记 `fixture-verified`，不得标记 `production-verified`。

## 后续 AI 执行协议

1. 开始改动前先读取 `docs/README.md`、`docs/architecture/README.md`、本文和目标模块 README。
2. 先补契约测试，再修改实现；每个小任务保持一个垂直切片。
3. 不新增第二套 Mission/Session 状态，不在 Renderer 中加入业务判断。
4. 不把 mock 响应、合成成功、静默空列表当作真实成功。
5. 任何新增事件、字段、错误码或 provider 能力都必须版本化并有兼容测试。
6. 真实失败记录到 `docs/development/ai-problem-solving-log.md`，包括根因、修复、验证命令和残余风险。
7. 阶段完成前运行受影响模块和完整阶段矩阵；GitHub Actions、npm registry、provider nightly 未实际运行时必须明确写 `未验收`。
