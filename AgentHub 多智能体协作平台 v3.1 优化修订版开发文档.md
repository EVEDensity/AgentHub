# AgentHub 多智能体协作平台 v3.1 优化修订版开发文档

> 基于《AgentHub 多智能体协作平台 v3.0 最终可行性开发文档》全文修订。本文档严格保留原有架构、技术栈、前后端代码逻辑、数据库设计、通信协议与业务流程，不推翻既有可运行实现，仅围绕竞赛评审、工业级落地与学术创新三重维度进行优化增强、逻辑补全、细节完善与规范统一。

---

## 0. 修订原则与边界

### 0.1 保留原则

本次优化不改变以下既有基础：

- 前端技术栈：Next.js 13 Pages Router、React 18、TypeScript、Tailwind CSS、Monaco Editor。
- 后端技术栈：FastAPI、Python、PostgreSQL、Redis、ChromaDB。
- 交互形态：类飞书 / 微信 IM 聊天即开发入口。
- 核心架构：三层联邦自治架构。
- 核心协议：自适应符号化蒸馏通信协议。
- 核心调度：动态稀疏激活，多 Agent 按需唤醒。
- 业务闭环：@指令调度 → DAG 拆解 → Agent 协作 → 代码生成 → Diff 校验 → 预览 → Git 提交 → 部署。
- 现有可运行代码：仅增强，不重构核心框架。

### 0.2 修订目标

1. 架构冗余收敛与核心创新可落地性。
2. 前后端异常处理、WebSocket 稳定性、多 Agent 冲突解决。
3. IM 聊天即开发的产品闭环。
4. 数据库关联、索引、冗余清理与符号消息读写效率。
5. 飞书暖色调设计系统、性能、懒加载与移动端适配。
6. Git、Diff、预览、部署的全链路闭环。
7. 豆包、Qwen、DeepSeek 等国产大模型接入能力。
8. 权限、安全、密钥加密与操作审计。
9. 容错、降级、风险防控与工程可用性。
10. 竞赛结题、产业落地与测试部署路线图。

---

## 1. 项目定位优化

### 1.1 一句话定位

AgentHub 是一个以 IM 聊天为入口、以三层联邦多智能体为执行内核、以符号蒸馏通信降低协作成本、以动态稀疏激活提升系统效率的 AI 软件工程协作平台。

### 1.2 面向竞赛评审的价值表达

| 维度 | 优化表达 |
|---|---|
| 创新性 | 不只是 Agent 调用平台，而是提出“IM 入口 + 三层联邦 + 符号蒸馏 + 稀疏激活”的系统级协作范式 |
| 完整性 | 覆盖任务输入、DAG 拆解、Agent 协作、代码生成、Diff 校验、预览、Git、部署、审计的闭环 |
| 可落地性 | 技术栈成熟，可本地运行，可接入国产模型，可扩展为企业研发协作平台 |

### 1.3 工业落地定位

- 研发需求入口统一：所有任务通过 IM 输入，减少在需求文档、代码仓库、部署平台之间切换。
- 多角色协作标准化：Architect、CodeGen、Review、Test、Deploy 角色职责清晰。
- 风险操作可审计：Git 提交、部署、权限变更均可记录。
- 模型可替换：支持 OpenAI、Anthropic、Ollama 以及豆包、Qwen、DeepSeek 等国产模型。
- 成本可控：通过符号蒸馏与稀疏激活减少上下文传输和模型调用浪费。

### 1.4 学术创新定位

1. **分层联邦协作结构**：将多 Agent 从平级群聊升级为元调度、领域主 Agent、微子 Agent 的三级体系。
2. **符号蒸馏通信协议**：用结构化摘要、向量索引、保真度评分替代原始长上下文传递。
3. **动态稀疏激活机制**：以任务相关性、角色依赖和资源状态作为 Agent 激活条件。
4. **IM 驱动的开发闭环范式**：将自然语言、代码变更、预览部署和版本管理统一到同一交互空间。

---

## 2. 架构优化：保留三层联邦，收敛冗余流程

### 2.1 原架构保留

继续采用：

```text
IM 交互层
  ↓
消息路由与适配层
  ↓
联邦智能层
  ↓
工具与基础设施层
```

### 2.2 冗余逻辑收敛

| 模块 | 唯一职责 | 不再承担 |
|---|---|---|
| 前端 IM | 输入采集、状态展示、用户确认 | 不做最终路由决策 |
| MessageRouter | @语义解析、目标 Agent 判定、冲突检测入口 | 不直接执行模型调用细节 |
| Orchestrator | DAG 拆解、依赖编排、失败恢复 | 不承担具体代码生成 |
| Domain Agent | 领域执行，如 CodeGen / Review / Test | 不直接修改全局 DAG |
| Micro Agent | 摘要、格式化、Diff 辅助、校验等短任务 | 不持久化长期状态 |
| SessionManager | 会话上下文索引、活跃连接映射 | 不保存完整原文历史 |
| SymbolicStore | 符号消息、摘要、保真度、向量索引 | 不保存 API Key |
| GitService | 分支、Diff、提交、回滚 | 不做前端展示 |

### 2.3 三层联邦架构强化

#### 元调度层 Orchestrator

职责：解析用户意图、匹配 DAG 模板、生成任务拓扑图、判断人工确认、处理失败重试、决定领域 Agent 激活。

新增工程约束：

- 每个任务必须生成 `task_fingerprint_id`。
- 每个 DAG 节点必须声明 `domain`、`dependencies`、`risk_level`、`write_scope`。
- 并行节点不得写入同一文件路径。
- 高风险节点必须经过 HumanInTheLoop 确认。

#### 领域主 Agent 层

| Agent | 输入 | 输出 | 禁止行为 |
|---|---|---|---|
| Architect | 用户意图、项目结构摘要 | 技术方案、文件影响范围 | 不直接写代码 |
| CodeGen | 架构方案、上下文索引 | 代码文件、Diff 草案 | 不直接提交 Git |
| Review | Diff、规范、风险策略 | 审查意见、风险等级 | 不修改部署配置 |
| Test | 代码变更、测试策略 | 测试结果、失败原因 | 不绕过 Review |
| Deploy | 已确认 Diff、部署目标 | 预览 URL、部署状态 | 不部署未审查代码 |

#### 微子 Agent 层

微子 Agent 用于摘要压缩、结构化提取、Diff 格式化、敏感信息扫描、单文件静态检查、日志归因。其原则是无持久身份、无长期记忆、执行结果必须通过符号消息返回。

---

## 3. 符号蒸馏通信协议优化

### 3.1 协议保留

保留 v3.0 的核心字段：`task_fingerprint_id`、`core_summary`、`extended_summaries`、`key_params`、`knowledge_vector_idx`、`confidence`、`fidelity_score`、`distillation_model`、`source_trace`。

### 3.2 新增工程字段

```json
{
  "protocol_version": "symbolic-v1",
  "session_id": "session-1",
  "task_id": "uuid",
  "sender_role": "CodeGen",
  "receiver_role": "Review",
  "intent_type": "code_generation",
  "risk_level": "L2",
  "write_scope": ["app/api/health.py"],
  "requires_human_confirm": false,
  "expires_at": "2026-05-16T12:00:00Z"
}
```

### 3.3 保真度闭环

| 保真度 | 系统行为 |
|---|---|
| ≥ 0.85 | 正常进入下一 Agent |
| 0.70 - 0.85 | 继续执行，但前端展示黄色提示 |
| 0.55 - 0.70 | 自动拉取扩展摘要或向量上下文补充 |
| < 0.55 | 阻断流程，要求 Orchestrator 重新蒸馏或人工确认 |

### 3.4 长会话记忆优化

长会话分为三层：短期上下文存 Redis，任务级符号记忆存 PostgreSQL，长期知识记忆存 ChromaDB。检索优先级：当前输入 → 最近消息 → 任务符号记忆 → 向量知识 → 原始文件片段。

## 4. IM 聊天即开发产品逻辑强化

### 4.1 @指令语义识别

原始 `@CodeGen xxx` 解析保留，但增强为三类语义：

| 类型 | 示例 | 路由 |
|---|---|---|
| 显式 Agent | `@CodeGen 生成 health 路由` | 直接路由 CodeGen |
| 隐式意图 | `帮我做一个登录页面` | Orchestrator 判定 Architect → CodeGen |
| 多 Agent 协作 | `@Review @Test 检查这次改动` | Orchestrator 生成 Review/Test DAG |

### 4.2 @指令解析规则

后端 MessageRouter 为最终权威：提取所有 `@Agent`，校验 Agent 是否存在、是否可用，判断用户权限是否允许调用，判断是否需要 Orchestrator 接管，生成路由计划并写入审计。前端只做 @自动补全、Agent 标签展示，不做权限最终判断。

### 4.3 会话层级管理

建议会话拆成四层：

```text
Workspace
  └── Project
       └── Session
            └── Task / DAG
```

当前代码可先保留 `session-1`，后续平滑扩展 `workspace_id`、`project_id`、`session_id`、`task_id`。

### 4.4 消息类型规范

| type | 用途 |
|---|---|
| text | 普通消息 |
| code | 代码展示 |
| diff | Git Diff 展示 |
| system | 系统通知 |
| task_update | DAG 进度 |
| audit | 审计提示 |
| preview | 预览 URL |
| deploy | 部署状态 |

---

## 5. 前后端交互漏洞与异常处理补全

### 5.1 REST API 统一响应格式

```json
{
  "success": true,
  "data": {},
  "error": null,
  "traceId": "uuid",
  "timestamp": "2026-05-16T12:00:00Z"
}
```

失败响应：

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "MODEL_CONFIG_INVALID",
    "message": "模型配置不可用",
    "retryable": false
  },
  "traceId": "uuid",
  "timestamp": "2026-05-16T12:00:00Z"
}
```

### 5.2 前端异常展示规范

| 场景 | 前端表现 |
|---|---|
| 网络失败 | 顶部 notice + 重试按钮 |
| 鉴权失败 | 跳回登录页，不清空本地草稿 |
| WebSocket 断开 | 显示“重连中”，消息进入失败重试队列 |
| Agent 失败 | 消息气泡展示失败原因与重试动作 |
| 高风险操作 | 弹窗确认 + 审计提示 |
| Diff 冲突 | DiffBubble 高亮冲突块 |

### 5.3 WebSocket 高并发稳定性

保留 WebSocket + Redis Pub/Sub 架构，增加连接心跳、消息 ACK、重连恢复、背压控制、分片传输与连接隔离。

- 客户端每 25 秒发送 ping，服务端返回 pong。
- 每条消息带 `message_id`，服务端确认后前端标记已送达。
- 重连后前端发送最近 `last_message_id`，后端补发缺失消息。
- 单会话消息积压超过阈值时降级为任务状态摘要。
- 大代码块和长 Diff 分片传输，前端合并后渲染 Monaco。
- 服务端按 `session_id + user_id + connection_id` 管理连接。

### 5.4 多 Agent 协作冲突解决

| 冲突 | 处理 |
|---|---|
| 文件写冲突 | `write_scope` 互斥锁，同一文件同一时间只允许一个写入 Agent |
| 逻辑冲突 | Orchestrator 汇总意见，生成仲裁消息 |
| 流程冲突 | DAG 状态机阻断非法迁移 |
| 用户插队 | 生成新任务或追加到当前任务，必须确认 |

---

## 6. 数据库设计优化

### 6.1 保留原表

保留 `users`、`sessions`、`agent_registry`、`tasks`、`dag_templates`、`symbolic_messages`、`audit_log`、`model_configs`。

### 6.2 建议新增字段

```sql
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS project_id UUID;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS title TEXT DEFAULT '默认会话';
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS last_message_at TIMESTAMP;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS archived BOOLEAN DEFAULT FALSE;

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS task_fingerprint_id UUID;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS risk_level TEXT DEFAULT 'L1';
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS retry_count INT DEFAULT 0;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS error_message TEXT;

ALTER TABLE symbolic_messages ADD COLUMN IF NOT EXISTS protocol_version TEXT DEFAULT 'symbolic-v1';
ALTER TABLE symbolic_messages ADD COLUMN IF NOT EXISTS session_id UUID;
ALTER TABLE symbolic_messages ADD COLUMN IF NOT EXISTS intent_type TEXT;
ALTER TABLE symbolic_messages ADD COLUMN IF NOT EXISTS write_scope JSONB;
```

### 6.3 索引设计

```sql
CREATE INDEX IF NOT EXISTS idx_sessions_last_message ON sessions(last_message_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_session_status ON tasks(session_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_fingerprint ON tasks(task_fingerprint_id);
CREATE INDEX IF NOT EXISTS idx_symbolic_task_time ON symbolic_messages(task_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_symbolic_fingerprint ON symbolic_messages(fingerprint_id);
CREATE INDEX IF NOT EXISTS idx_audit_user_time ON audit_log(user_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_model_provider_active ON model_configs(provider, is_active);
```

### 6.4 冗余清理策略

| 数据 | 清理策略 |
|---|---|
| WebSocket 临时消息 | Redis TTL 24 小时 |
| 低价值中间摘要 | 任务完成 7 天后压缩 |
| 原始长上下文 | 默认不落库，只存向量索引与审计 hash |
| 失败任务日志 | 保留最近 30 天 |
| API Key 明文 | 禁止落库 |

## 7. 前端设计系统与性能优化

### 7.1 飞书暖色调设计系统标准化

保留现有 `warm` 色阶与组件类，统一规范：

| Token | 用途 |
|---|---|
| `warm-50` | 页面背景 |
| `warm-100/150` | 分割线、浅底卡片 |
| `warm-700/800/900` | 正文、标题、深色容器 |
| `primary-500` | 主操作、链接、进度条 |
| `success-500` | 成功状态 |
| `warning-500` | 风险提示 |
| `danger-500` | 错误/高危 |

### 7.2 Tailwind 配置注意事项

迁移 TypeScript 后必须确保 Tailwind 扫描：

```js
content: [
  './pages/**/*.{js,jsx,ts,tsx}',
  './components/**/*.{js,jsx,ts,tsx}',
  './styles/**/*.css'
]
```

否则会出现页面裸样式、结构混乱的问题。

### 7.3 组件懒加载

建议懒加载 Monaco Editor、PreviewSidebar、Admin 页面重型表格、GeneratedFilesPanel。聊天基础 UI 首屏加载，代码 / Diff / 预览 / 管理后台按需加载。

### 7.4 移动端适配

| 页面 | 移动端策略 |
|---|---|
| 左侧导航 | 抽屉式展开 |
| 聊天区 | 全屏主视图 |
| DiffBubble | 横向滚动 + 全屏查看按钮 |
| PreviewSidebar | 全屏弹层 |
| Admin | 卡片化表单，表格折叠 |

---

## 8. Git、Diff、预览、部署闭环优化

### 8.1 标准链路

```text
CodeGen 生成文件
  ↓
GeneratedFilesPanel 展示文件
  ↓
GitService 生成 Diff
  ↓
Review Agent 审查 Diff
  ↓
用户确认
  ↓
Git Commit
  ↓
PreviewSandbox 生成预览 URL
  ↓
Deploy Agent 一键部署
  ↓
AuditLog 记录操作
```

### 8.2 Diff 校验规则

提交前必须检查：是否存在高危文件修改、是否包含 API Key / Token / 密码、是否包含二进制或异常大文件、是否跨越 Agent 声明的 `write_scope`、是否通过 Review Agent 最低分数阈值。

### 8.3 预览机制

| 类型 | 场景 |
|---|---|
| 本地预览 | 生成页面、组件时直接 iframe 查看 |
| 沙箱预览 | 运行临时服务，隔离用户环境 |
| 部署预览 | CI/CD 生成预览链接 |

### 8.4 一键部署约束

Deploy Agent 必须满足：当前任务状态为 `SUCCESS`，Review 通过，Test 通过或用户强制确认，Git 工作区干净，审计日志已记录。

---

## 9. 国产大模型与字节生态适配

### 9.1 Provider 扩展

| Provider | baseUrl 示例 | 适配重点 |
|---|---|---|
| DeepSeek | `https://api.deepseek.com/v1` | 代码生成、推理性价比 |
| Qwen | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 中文理解、企业落地 |
| 豆包 | `https://ark.cn-beijing.volces.com/api/v3` | 字节生态适配、国产化 |
| MiniMax | `https://api.minimax.chat/v1` | 长文本、中文对话 |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | 中文知识与工具调用 |

### 9.2 AdapterManager 统一策略

所有模型适配器统一暴露：

```text
execute_prompt(prompt, options)
stream_chat(messages, options)
test_connection()
estimate_cost(tokens)
```

### 9.3 国产模型角色建议

| Agent | 推荐模型 |
|---|---|
| Orchestrator | DeepSeek-R1 / Qwen-Max / Doubao-pro |
| CodeGen | DeepSeek-Coder / Qwen-Coder |
| Review | DeepSeek-R1 / Qwen-Plus |
| Test | Qwen-Coder / 本地小模型 |
| Deploy | 规则引擎 + 中等模型 |
| Micro Agent | Ollama / 本地量化模型 |

---

## 10. 安全机制与审计流程

### 10.1 权限分级

| 角色 | 权限 |
|---|---|
| admin | 模型配置、角色绑定、审计查看、部署确认 |
| developer | 发送任务、查看 Diff、确认普通提交 |
| reviewer | 审查 Diff、批准高风险代码 |
| tester | 执行测试、查看测试结果 |
| guest | 只读查看会话 |

### 10.2 API Key 加密存储

要求：前端不缓存 API Key，后端接收后立即加密，数据库只保存密文，日志中永不打印 Key，测试连接只返回成功/失败，不返回敏感细节。

### 10.3 操作审计

必须审计：登录 / 登出、模型配置新增 / 修改、Agent 角色绑定修改、Git 分支创建 / 提交 / 回滚、高风险操作确认、部署触发、权限变更。

审计日志字段：

```text
user_id, agent_id, action, risk_level, decision, content_hash, trace_id, timestamp
```

## 11. 风险防控、容错与降级运行

### 11.1 风险分级

| 风险等级 | 示例 | 处理 |
|---|---|---|
| L1 | 生成普通代码 | 自动执行 |
| L2 | 修改业务逻辑 | Review 通过后执行 |
| L3 | 修改认证、权限、数据库 | 人工确认 |
| L4 | 部署、删除、回滚生产数据 | 管理员二次确认 |

### 11.2 降级策略

| 故障 | 降级 |
|---|---|
| 高阶模型不可用 | 切换备用模型 |
| WebSocket 断开 | REST 轮询任务状态 |
| Redis 不可用 | 单机内存会话管理 |
| 向量库不可用 | 使用 PostgreSQL 最近摘要 |
| Monaco 加载失败 | fallback 为 `<pre><code>` 展示 |
| Deploy 失败 | 保留预览与 Git 提交，不执行生产部署 |

### 11.3 任务状态机

```text
PENDING → RUNNING → REVIEWING → WAITING_CONFIRM → SUCCESS
                          ↓
                       FAILED → RETRYING → RUNNING
                          ↓
                       CANCELLED
```

---

## 12. 测试方案

### 12.1 前端测试

- 登录状态恢复。
- WebSocket 断线重连。
- @Agent 自动补全。
- DiffBubble 全宽展示。
- GeneratedFilesPanel 文件切换。
- Admin 模型配置表单。
- 移动端布局。

### 12.2 后端测试

- MessageRouter @解析。
- DAG 循环检测。
- AdapterManager 多模型测试。
- API Key 加密存取。
- GitService 分支、Diff、提交。
- WebSocket 多连接广播。
- 审计日志写入。

### 12.3 集成测试

核心链路：

```text
登录 → 发送 @CodeGen → WebSocket 返回代码 → 展示 Diff → 确认提交 → Git commit → 获取预览 URL → 审计日志可查
```

### 12.4 竞赛演示测试用例

1. `@CodeGen 生成 FastAPI health 路由文件，保存为 health_router.py`
2. 展示代码生成与 Diff。
3. 展示保真度评分。
4. 点击确认提交。
5. 打开预览面板。
6. 管理后台展示模型配置与审计日志。
7. 展示断线重连与失败重试队列。

---

## 13. 部署方案

### 13.1 本地开发

```text
FastAPI: http://localhost:8000
Next.js: http://localhost:3000
WebSocket: ws://localhost:8000/ws/{session_id}
```

### 13.2 生产部署建议

| 组件 | 部署 |
|---|---|
| Next.js | Vercel / Docker / Nginx 静态代理 |
| FastAPI | Uvicorn + Gunicorn |
| PostgreSQL | 云数据库或 Docker Compose |
| Redis | 云 Redis 或本地 Redis |
| ChromaDB | 独立向量服务 |
| Git Sandbox | 隔离容器 |

### 13.3 环境变量

```env
DATABASE_URL=postgresql://...
REDIS_URL=redis://...
SECRET_KEY=...
ENCRYPTION_KEY=...
CHROMA_URL=...
FRONTEND_ORIGIN=http://localhost:3000
```

---

## 14. 优化后路线图

| 阶段 | 目标 | 交付 |
|---|---|---|
| P0 | 保持现有系统稳定运行 | 登录、IM、WebSocket、基础 Agent 可用 |
| P1 | 补齐 TS 前端与暖色 UI | 全部页面 TSX、Tailwind 正常、Diff 全宽 |
| P2 | 强化 Agent 调度 | @语义、DAG 状态机、冲突检测 |
| P3 | 补齐工程闭环 | Git、Diff、预览、部署、审计 |
| P4 | 强化模型生态 | 豆包、Qwen、DeepSeek、Ollama 统一适配 |
| P5 | 竞赛交付 | 演示脚本、测试报告、部署文档、答辩材料 |
| P6 | 产业落地 | 权限、租户、监控、成本统计、企业私有化 |

---

## 15. 结题与答辩表达建议

### 15.1 技术创新总结

AgentHub 的核心创新不在于“调用多个大模型”，而在于建立了一个可运行、可审计、可扩展的多智能体协作操作系统雏形：

- 用 IM 降低用户使用门槛。
- 用三层联邦提升协作秩序。
- 用符号蒸馏降低通信成本。
- 用动态稀疏激活降低资源消耗。
- 用 Git / Diff / 预览 / 部署形成工程闭环。

### 15.2 工程落地总结

项目已具备从 Demo 到生产原型的演进路径：前后端分离清晰，数据库模型可扩展，模型适配器可插拔，安全审计可落地，国产模型生态可接入，可部署、可测试、可演示。

### 15.3 学术价值总结

可作为多智能体协作系统研究样例，围绕以下方向继续扩展：

- 多 Agent 协作中的信息压缩与保真度评估。
- 任务 DAG 与自然语言意图之间的映射。
- 动态稀疏激活对成本、延迟、质量的影响。
- IM 交互范式下的人机协同开发效率评估。

---

## 16. 与 v3.0 的关系

本 v3.1 文档是 v3.0 的增强修订版：不替换原架构，不推翻原技术栈，不删除原业务流程，不要求重写现有可运行代码，只补齐工程细节、异常处理、安全审计、性能优化、国产模型适配与竞赛表达。

后续代码生成与迭代应遵循：

```text
v3.0 = 基础蓝图与可运行代码逻辑
v3.1 = 工程增强、竞赛优化、产业落地细化
```

---

**文档结束。**



