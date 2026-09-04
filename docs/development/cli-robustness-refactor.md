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
| 第一阶段 | stream/tools 逻辑彻底分离 | partial | 当前 Harness 的 stream 与 tools 仍存在隐式耦合；必须补统一 `ModelRequest` |
| 第一阶段 | 工具状态使用 `call_id` | not-started | 当前 reducer 主要按工具名合并，同名并发调用有覆盖风险 |
| 第一阶段 | 稳定错误分类 | partial | 已有部分重试判断，尚未形成统一 Transport/Protocol/Auth/Retryable 错误层 |
| 第二阶段 | 独立 HTTP transport | not-started | 当前 `MissionControlClient` 同时承担认证、HTTP、业务 API |
| 第二阶段 | 独立 SSE client | partial | SSE parser 已独立，连接生命周期仍在 `MissionControlClient` |
| 第二阶段 | Mission/Decision/Artifact API 拆分 | not-started | 仍是单一客户端门面 |
| 第二阶段 | 每个 API 的契约测试 | partial | 已有 CLI/API 测试，尚未按 API 边界完整覆盖 |
| 第二阶段 | 统一重试、超时、认证 | partial | 存在局部超时和 provider 重试，尚未集中到 Transport |
| 第三阶段 | 可靠性测试矩阵 | partial | 已覆盖部分 parser、重复/乱序事件；真实 TTY、断线恢复、registry 验收仍缺 |

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

