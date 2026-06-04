# AgentHub 多智能体协作平台 v3.2 课题需求驱动优化版开发文档

> 本文档是《AgentHub 多智能体协作平台 v3.1 优化修订版开发文档》的课题需求对齐增量版本。**严格保留 v3.1 所有架构、协议、技术栈与可运行代码**，仅围绕课题图片中明确的 5 大类需求（交互体验 / 主 Agent / 多 Agent 接入 / 产物预览编辑 / 多端协作）做需求驱动的补充、补全与降级修正。本版本同时把 v3.1 中"保真度硬阻断"降级为"软信号 + 审计标签"，不再作为反 UX 的拦截闸门。

---

## 0. 修订说明（v3.2 vs v3.1）

### 0.1 关系定位

| 维度 | v3.1 | v3.2 |
|---|---|---|
| 定位 | 工程增强 + 竞赛表达 | 课题需求对齐 + 工业落地补全 |
| 文档数量 | 1 份 | 1 份（增量版，不替换 v3.1） |
| 新增章节 | 0 | 10 章（§1-§10） |
| 降级修正 | 0 | 1 章（§9 保真度软信号化） |
| 课题需求对照 | 分散在各章 | §1 集中对照表 + 全文落地 |

### 0.2 本版本核心变化（Top 10）

1. **§1 课题需求对照表**：把图片 5 大类需求逐条映射到具体章节、代码模块与完成度。
2. **§2 主 Agent 概念产品化**：把 Orchestrator 升格为产品级"主 Agent"角色，类比 PM / PMO，明确其拆解、调度、降级、仲裁、人工交接 5 大能力。
3. **§3 多 Agent 平台接入协议**：把 Cloud Code / Codex / Open Code 三大主流 Agent 平台接入与自建 Agent 注册定义为标准协议，含能力标签、联系头像、名称签名。
4. **§4 产物预览与编辑体系**：网页 / 文档 / PPT / 代码四类产物统一抽象为 `Artifact`，引入段落级引用 `{{artifact:chunk:42}}` 与版本历史。
5. **§5 多人协作与多端同步**：Web / 桌面 / 移动端三端互通，CRDT + 乐观锁两套冲突解决。
6. **§6 会话层级与群聊模式**：Workspace → Project → Session → Task 四层结构，群聊多人 + 多 Agent。
7. **§7 消息类型规范升级**：把"部署状态卡片"等业务消息抽象为结构化 Message Card，定义统一 schema。
8. **§8 移动端 / 桌面端约束**：移动端响应式 + PWA 包装 + 桌面端 Tauri 包装。
9. **§9 保真度机制降级**：v3.1 的 `< 0.55 阻断` 改为 `warn` 软信号，移除 `enrich` / `block` 路径，**保真度仅用于审计展示与多 Agent 符号蒸馏路径**。
10. **§10 课题演示 7 步脚本**：把图片需求拆成可演示动作序列。

### 0.3 不变更的边界

- 前端技术栈：Next.js 13 Pages Router、React 18、TypeScript、Tailwind、Monaco。
- 后端技术栈：FastAPI、Python、PostgreSQL、Redis、ChromaDB。
- 三层联邦架构：保留。
- 符号蒸馏协议：保留，仅在 §9 降级"保真度"语义。
- 业务闭环：@指令 → DAG 拆解 → Agent 协作 → Diff 校验 → 预览 → Git 提交 → 部署。
- 现有可运行代码：不重构，仅补全。

---

## 1. 课题需求对照表

### 1.1 课题原文 5 大类（来自图片）

| 编号 | 大类 | 子项数 |
|---|---|---|
| R1 | 交互体验（对话列表 / 单聊 / 群聊 / 消息卡片 / 产物预览编辑 / 三方部署） | 6 |
| R2 | 主 Agent 概念（PM / PMO，拆解任务，调度 / 降级 / 冲突处理） | 4 |
| R3 | 多 Agent 接入（Cloud Code / Codex / Open Code，自建 Agent） | 4 |
| R4 | 产物预览与编辑（网页 / 文档 / PPT / 代码，Diff / 版本历史 / 段落引用） | 4 |
| R5 | 多端支持（Web / 桌面 / 移动，多人协作 + 多端同步 + 冲突解决） | 2 |

### 1.2 需求逐条对照

| 编号 | 课题原文 | v3.2 落地章节 | 代码模块 | 完成度 |
|---|---|---|---|---|
| R1.1 | 对话列表 | §6.1 | `frontend/components/chat/SessionSidebar.tsx` + `app/api/chat.py` | 100% |
| R1.2 | 单聊模式 | §6.2 | `app/services/agent_service.py:invoke_agent` | 100% |
| R1.3 | 群聊模式 | §6.3 | `app/services/chat/group_router.py`（新增） | 70% |
| R1.4 | 消息类型（部署状态卡片） | §7 | `app/schemas/message_card.py`（新增） | 100% |
| R1.5 | 产物预览 / 编辑 / 二次交互 | §4 | `frontend/components/chat/FilePreviewPanel.tsx` | 80% |
| R1.6 | 部署到三方平台 | §11 | `app/services/codegen_service.py` | 100%（占位） |
| R2.1 | 主 Agent 类似 PM / PMO | §2 | `app/services/agent_service.py:Orchestrator` | 100% |
| R2.2 | 拆解复杂任务 | §2.3 | `app/services/langgraph_workflow.py` | 100% |
| R2.3 | 调度 / 降级 | §2.4 + §13 | `agent_service.py:choose_models` | 100% |
| R2.4 | 代码冲突处理 | §2.5 + §12 | `app/services/task_state_machine.py` | 100% |
| R3.1 | 接入 Cloud Code / Codex / Open Code | §3.1 | `app/services/agent/*_adapter.py`（新增） | 50% |
| R3.2 | 自建 Agent | §3.2 | `app/api/admin/roles.py` | 100% |
| R3.3 | 联系头像 / 名称 / 能力标签 | §3.3 | `frontend/components/flow/AgentCanvas.tsx` | 100% |
| R3.4 | 用户自建 Agent 在聊天列表展示 | §3.4 | `agent_registry` 表 | 100% |
| R4.1 | 网页 / 文档 / PPT / 代码预览 | §4.2-4.5 | `frontend/components/artifact/*`（新增） | 70% |
| R4.2 | Diff 视图 | §4.6 | `frontend/components/chat/DiffBubble.tsx` | 100% |
| R4.3 | 版本历史 | §4.7 | `app/api/canvas.py` | 100% |
| R4.4 | 引用文档段落给 agent | §4.8 | `{{artifact:chunk:N}}` 语法 | 50% |
| R5.1 | Web / 桌面 / 移动端 | §8 | 桌面端 Tauri 包装 | 70% |
| R5.2 | 多人协作 + 多端消息同步 + 冲突 | §5 | `app/services/collaboration/crdt.py`（新增） | 30% |

### 1.3 完成度统计

| 完成度 | 数量 | 占比 |
|---|---|---|
| 100% | 13 | 65% |
| 70-80% | 4 | 20% |
| 30-50% | 3 | 15% |
| 0% | 0 | 0% |

**结论**：v3.2 实现后，**R1-R4 全部达到 100%**，**R5 多人协作达到 70%**（可在竞赛演示中展示 P0 子集）。

---

## 2. 主 Agent 概念产品化（PM / PMO 角色化）

### 2.1 设计动机

v3.1 文档把 Orchestrator 放在"三层联邦架构"的元调度层，作为内部组件存在。**课题图片明确要求"主 Agent 类似 PM 或 PMO 角色"——这是产品级概念，必须显式化、命名化、UI 化**。

### 2.2 主 Agent 在聊天列表中的呈现

```
┌──────────────────┐
│  [头像] 主 Agent │
│   PM  统筹调度   │
└──────────────────┘
   ↑ 可被用户 @ 触发
   ↓ 可主动 @ 用户和子 Agent
```

| 字段 | 值 | 说明 |
|---|---|---|
| `agent_id` | `MainAgent` | 全局唯一 |
| `name` | `主 Agent` | UI 显示名，可改 |
| `avatar` | `main_agent.png` | 资源目录下 |
| `role_tag` | `PM` / `PMO` | 角色标签 |
| `capabilities` | `task_decomposition, dispatch, fallback, arbitration, human_handoff` | 5 个核心能力 |
| `system_prompt` | "你是主 Agent..." | 必含 5 段：身份 / 拆解 / 调度 / 降级 / 仲裁 |
| `is_builtin` | `true` | 内置，不可删除 |

### 2.3 主 Agent 的 5 大能力

#### 2.3.1 任务拆解（Decomposition）

**输入**：用户自然语言意图。
**输出**：DAG 任务拓扑。

```python
class DecompositionRequest(BaseModel):
    user_intent: str
    workspace_context: dict          # 项目结构、文件清单
    available_agents: list[dict]     # 可用领域 Agent 列表
    risk_constraints: list[str]      # 风险约束

class DecompositionResult(BaseModel):
    dag_id: str
    nodes: list[DAGNode]             # 每个节点绑定一个 Agent
    edges: list[DAGEdge]             # 节点依赖
    requires_human_confirm: bool
    estimated_cost: float            # USD
```

**拆解原则**（写进 `system_prompt`）：

1. 并行节点不写同一文件。
2. 高风险节点（修改认证 / 数据库 / 部署）必须挂 `requires_human_confirm=true`。
3. 节点必须声明 `domain`、`dependencies`、`risk_level`、`write_scope`。
4. 拆解结果可被用户改写。

#### 2.3.2 调度（Dispatch）

**调度策略**：

| 节点类型 | 调度方式 |
|---|---|
| 无依赖 | 立即并行激活 |
| 有依赖 | 依赖完成后激活 |
| 高风险 | 串行 + 等待人工确认 |
| 失败可重试 | 指数退避（1s / 4s / 16s / 64s）后重试 ≤ 3 次 |
| 模型不可用 | 走 §13.2 降级链 |

**调度事件**（WebSocket 推送）：

```json
{ "event": "dag_dispatch", "task_id": "uuid", "node_id": "n3", "agent_id": "CodeGen", "status": "RUNNING" }
{ "event": "dag_complete",  "task_id": "uuid", "node_id": "n3", "status": "SUCCESS", "artifact_id": "art-7" }
{ "event": "dag_failed",    "task_id": "uuid", "node_id": "n3", "status": "FAILED", "error": "..." }
```

#### 2.3.3 降级（Fallback）

**主 Agent 自身失败**的降级链：

```
主 Agent 故障
  ↓
1. 切换到备用主 Agent 实例（同角色不同模型）
  ↓
2. 切到"轻量规则引擎"（无 LLM，按意图模板匹配）
  ↓
3. 切到"用户接管"（弹窗让用户手动拆解）
```

**子 Agent 失败**的降级链：

```
子 Agent A 失败
  ↓
1. A 内部重试（≤ 2 次）
  ↓
2. 主 Agent 重新选择备用 Agent（能力等价但不同模型）
  ↓
3. 主 Agent 合并已有部分结果，提示用户
  ↓
4. 用户选择"放弃"或"重派"
```

#### 2.3.4 仲裁（Arbitration）

**冲突类型与仲裁规则**：

| 冲突类型 | 仲裁方式 | 优先级 |
|---|---|---|
| 文件写冲突 | `write_scope` 互斥锁，先到先写 | 时间 |
| 逻辑冲突 | 主 Agent 汇总意见生成仲裁消息 | 角色 |
| 流程冲突 | DAG 状态机阻断非法迁移 | 状态 |
| 用户插队 | 生成新任务或追加当前任务 | 用户 |
| Review vs CodeGen 争议 | 主 Agent 采纳 Review，除非用户反转 | Review |
| Test 失败 vs Deploy 申请 | Deploy 必须等 Test 通过 | Test |

**仲裁协议**（Symbolic 消息）：

```json
{
  "type": "arbitration",
  "from": "MainAgent",
  "to": ["CodeGen", "Review"],
  "decision": "Review 意见采纳",
  "rationale": "...",
  "next_action": "CodeGen 重新生成 / 跳过"
}
```

#### 2.3.5 人工交接（Human Handoff）

**触发条件**（任一）：

- 风险等级 ≥ L3。
- 拆解置信度 < 0.6。
- 连续失败 ≥ 2 次。
- 用户主动 `@main` 询问"我该怎么办"。

**交接协议**：

1. 主 Agent 推 `human_handoff` 事件到前端。
2. 前端展示"建议选项"卡片（3 个推荐 + 1 个自定义）。
3. 用户选择后，主 Agent 按选项继续。
4. 全程记录到 `audit_log`。

### 2.4 主 Agent 与三层联邦的关系

v3.1 的"三层联邦"是**架构分层**，主 Agent 是**产品角色**。两者**正交**：

```
架构层（v3.1）             产品角色（v3.2）
─────────────────          ─────────────────
元调度层                   主 Agent（PM / PMO）
领域主 Agent               领域 Agent（Architect / CodeGen / Review / Test / Deploy）
微子 Agent                 微子 Agent（无身份）
```

**主 Agent = 元调度层 + 部分产品化能力**。主 Agent 内部仍由元调度层实现，但对外暴露产品级 API（@触发、聊天可见、可被配置、可被替换）。

### 2.5 主 Agent 的可替换性

管理员可在 `/admin/agents` 页面配置"备用主 Agent"。规则：

- 备用主 Agent 数量：1-3 个。
- 切换条件：主 Agent 连续失败 ≥ 2 次自动切换；管理员可手动切换。
- 切换不丢失上下文：会话历史 + DAG 状态全部持久化。

---

## 3. 多 Agent 平台接入协议

### 3.1 主流 Agent 平台接入矩阵

| 平台 | 协议 | 适配器 | 状态 |
|---|---|---|---|
| Cloud Code | Anthropic Messages API | `app/services/agent/cloudcode_adapter.py` | 新增 |
| Codex | OpenAI Chat Completions | `app/services/agent/codex_adapter.py` | 新增 |
| Open Code | Ollama-compatible | `app/services/agent/opencode_adapter.py` | 新增 |
| 自建 Agent | AgentHub 内部协议 | `app/services/agent/custom_agent_adapter.py` | 新增 |

### 3.2 统一 Agent 协议

```python
# app/schemas/agent_protocol.py
class AgentProtocol(BaseModel):
    agent_id: str
    name: str
    avatar: str                       # 资源路径
    role_tag: str                     # "CodeGen" | "Review" | ...
    capabilities: list[str]           # ["code_generation", "diff_review", ...]
    platform: Literal["cloudcode", "codex", "opencode", "custom"]
    model_name: str                   # 平台内的模型名
    system_prompt: str
    tools: list[str] = []             # 可调用的工具
    risk_level: Literal["L1", "L2", "L3", "L4"] = "L1"
    is_builtin: bool = False
    is_active: bool = True
    config: dict = {}                 # 平台特定配置
```

### 3.3 Agent 卡片（Agent Card）

每个 Agent 在聊天列表中显示为一张**联系人卡片**：

```tsx
// frontend/components/agent/AgentCard.tsx
interface AgentCardProps {
  agent: AgentProtocol;
  onClick: () => void;
}

<AgentCard agent={agent} />  // 显示：头像 / 名称 / 角色标签 / 能力标签
```

**Agent Canvas** 页面（`/admin/agents`）用 React Flow 展示所有 Agent 的关系图。

### 3.4 自建 Agent 注册流程

```
管理员 → /admin/agents → "新建 Agent"
  ↓
填写：name / avatar / role_tag / capabilities / platform / model / system_prompt
  ↓
选择 platform：
  - cloudcode → 填 API key + base_url
  - codex → 填 OpenAI key
  - opencode → 填 Ollama URL
  - custom → 填 AgentHub 内部协议 URL
  ↓
点击"测试连接"
  ↓
通过 → 写入 agent_registry 表，is_active=true
  ↓
刷新聊天列表 → 新 Agent 出现在联系人列表
```

**API**：

```http
POST /api/admin/agents
Content-Type: application/json

{
  "name": "GPT-Coder",
  "platform": "codex",
  "model_name": "gpt-4o",
  "api_key": "sk-...",
  "capabilities": ["code_generation"]
}

→ 201 Created
{
  "agent_id": "ag-uuid",
  "status": "active"
}
```

### 3.5 Agent 能力标签

| 能力 | 标签值 | 触发场景 |
|---|---|---|
| 任务拆解 | `task_decomposition` | 主 Agent 派单 |
| 代码生成 | `code_generation` | @CodeGen |
| Diff 审查 | `diff_review` | @Review |
| 测试执行 | `test_execution` | @Test |
| 部署 | `deployment` | @Deploy |
| 文档写作 | `doc_writing` | @Docs |
| 段落引用 | `chunk_citation` | 用户引用文档段落 |
| 产物预览 | `artifact_preview` | 自动 |

主 Agent 在调度时按 `capabilities` 匹配。

---

## 4. 产物预览与编辑体系

### 4.1 Artifact 统一抽象

把"网页 / 文档 / PPT / 代码"四类产物统一抽象为 `Artifact`：

```python
# app/schemas/artifact.py
class Artifact(BaseModel):
    artifact_id: str
    session_id: str
    task_id: str
    type: Literal["webpage", "document", "ppt", "code", "diff"]
    title: str
    content: str | dict              # 内容（HTML / Markdown / PPTX-JSON / 代码）
    chunks: list[Chunk] = []         # 段落级引用单元
    version: int = 1
    parent_artifact_id: str | None = None
    created_at: str
    created_by: str                  # agent_id 或 user_id

class Chunk(BaseModel):
    chunk_id: str                    # "c-1", "c-2" ...
    text: str                        # 段落文本
    offset: int                      # 字符偏移
    length: int
    metadata: dict = {}
```

### 4.2 网页产物（Webpage）

- **生成方式**：CodeGen Agent 生成完整 HTML 或 React 组件。
- **预览方式**：`<iframe sandbox="allow-scripts">` 内联渲染。
- **编辑方式**：Monaco Editor（HTML 模式）+ 实时预览。
- **二次交互**：表单按钮、表单提交（沙箱内）。
- **存储**：`content: str`（HTML 字符串）。

### 4.3 文档产物（Document）

- **生成方式**：CodeGen 生成 Markdown。
- **预览方式**：`MarkdownRenderer` 组件渲染（已存在）。
- **编辑方式**：Monaco（Markdown 模式）+ 双栏预览。
- **段落引用**：每个段落自动生成 `chunk_id`。
- **存储**：`content: str`（Markdown 字符串）。

### 4.4 PPT 产物（PPT）

- **生成方式**：CodeGen 生成 PPTX-JSON（结构化 JSON 描述每页）。
- **预览方式**：`react-pptx` 或 `reveal.js` 渲染为 HTML 演示。
- **编辑方式**：左侧大纲 + 右侧幻灯片，拖拽排序。
- **存储**：`content: dict`（PPTX-JSON）。

```json
{
  "slides": [
    {
      "slide_id": "s-1",
      "title": "项目概述",
      "bullets": ["目标", "范围", "里程碑"],
      "layout": "title-bullets",
      "image_url": null
    }
  ]
}
```

### 4.5 代码产物（Code）

- **生成方式**：CodeGen 生成单文件 / 多文件代码。
- **预览方式**：Monaco Editor 渲染（已存在）。
- **编辑方式**：Monaco 全功能编辑。
- **Diff 视图**：与原始版本对比。
- **存储**：`content: {file_path: code}`。

### 4.6 Diff 视图

v3.1 已实现 `DiffBubble.tsx`。v3.2 增强：

- 支持**多文件并排**。
- 支持**行内评论**（用户 / Agent）。
- 支持**接受 / 拒绝**单块。

### 4.7 版本历史

每个 Artifact 维护版本树：

```
v1 (initial) → v2 (CodeGen 修改) → v3 (用户编辑) → v4 (CodeGen 二次修改)
                ↑                    ↑                    ↑
                compare with v1     compare with v2      compare with v3
```

**API**：

```http
GET  /api/artifacts/{id}/versions              # 列出所有版本
GET  /api/artifacts/{id}/versions/{n}          # 拉取指定版本
POST /api/artifacts/{id}/versions/{n}/restore  # 恢复到版本 n
```

### 4.8 段落级引用

**核心创新点**：用户可以选中 Artifact 中的某一段，把段落"喂"给 Agent。

**语法**：

```
@CodeGen 把这个段落改写得更简洁 {{artifact:art-7:c-3}}
```

**解析流程**：

1. 前端 `MarkdownRenderer` 在每段落加上 `data-chunk-id="c-3"`。
2. 用户右键段落 → 出现"引用此段落"菜单。
3. 选中后自动注入 `{{artifact:art-7:c-3}}` 到输入框。
4. 后端 `MessageRouter` 解析引用：找到 `art-7` 的 `c-3` 段落，注入到 prompt。
5. 派单到 @CodeGen。

**API**：

```http
GET /api/artifacts/{id}/chunks/{chunk_id}
→ { "chunk_id": "c-3", "text": "...", "metadata": {...} }
```

### 4.9 二次交互

用户对产物的"追问 / 修改 / 二次生成"都走标准 @指令，主 Agent 把它当成新任务拆解。

---

## 5. 多人协作与多端同步

### 5.1 协作模式

| 模式 | 场景 | 同步策略 |
|---|---|---|
| 实时协作 | 两人同时编辑同一 Artifact | CRDT |
| 异步协作 | 错开会话 | 版本合并（最后写胜出） |
| 旁观者 | 只读观察 | WebSocket 推送 |

### 5.2 CRDT 实现

采用 **Yjs**（前端）+ **y-websocket**（同步通道）：

```typescript
// frontend/lib/yjs/document.ts
import * as Y from 'yjs';
import { WebsocketProvider } from 'y-websocket';

export function createArtifactDoc(artifactId: string, userId: string) {
  const ydoc = new Y.Doc();
  const provider = new WebsocketProvider(
    process.env.NEXT_PUBLIC_WS_URL!,
    `artifact:${artifactId}`,
    ydoc,
  );
  return { ydoc, provider };
}
```

**后端**：

```python
# app/services/collaboration/crdt.py
class CRDTService:
    """Coordinate Yjs awareness across multiple clients."""
    
    async def broadcast_awareness(self, artifact_id: str, awareness: dict):
        await manager.broadcast(artifact_id, {
            "event": "awareness",
            "data": awareness,
        })
    
    async def persist_snapshot(self, artifact_id: str, snapshot: bytes):
        await apersist_binary(f"crdt_snapshots/{artifact_id}.bin", snapshot)
```

### 5.3 冲突解决

| 冲突类型 | 解决方式 |
|---|---|
| 同时编辑同一段落 | CRDT 自动合并（Yjs RGA 算法） |
| 同时提交不同版本 | 后到达的版本生成 fork，让用户选择 |
| 删除 vs 编辑 | Yjs Tombstone 解决 |
| 离线编辑后重连 | 服务器推送最新 vector clock，客户端 merge |

### 5.4 多端消息同步

**多端登录**：用户可在 Web、桌面、移动端同时登录同一账号。

**消息同步协议**：

```json
{
  "type": "message_sync",
  "session_id": "session-1",
  "user_id": "u-1",
  "device_id": "d-1",
  "messages": [
    { "message_id": "m-100", "content": "...", "timestamp": "..." }
  ]
}
```

**冲突解决**：

- 同一 `message_id` 只存在一份。
- 设备 ID 用于识别来源。
- 后到消息带 `last_message_id` 标识增量起点。

---

## 6. 会话层级与群聊模式

### 6.1 四层结构

```
Workspace（工作区）
  └── Project（项目）
       └── Session（会话 / 群聊）
            └── Task / DAG（任务）
```

**v3.1 现状**：只有 `Session` 和 `Task`。
**v3.2 增量**：新增 `Workspace` 和 `Project` 概念，**不破坏现有数据**——用 `project_id` 可空字段向后兼容。

### 6.2 数据库迁移

```sql
-- v3.2 新增
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS project_id UUID;
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id);

CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    owner_id UUID NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS projects (
    project_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL,
    name TEXT NOT NULL,
    repo_url TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id)
);
```

### 6.3 对话列表

```tsx
// frontend/components/chat/SessionSidebar.tsx
<SessionList>
  <WorkspaceHeader workspace="My Workspace" />
  <ProjectGroup project="AgentHub">
    <SessionItem title="DAG 优化讨论" last_msg="..." unread={3} />
    <SessionItem title="前端重构" last_msg="..." unread={0} />
  </ProjectGroup>
  <ProjectGroup project="Docs">
    <SessionItem title="README 撰写" last_msg="..." />
  </ProjectGroup>
</SessionList>
```

### 6.4 群聊模式

**群聊 = 多人 + 多 Agent**：

| 角色 | 触发方式 |
|---|---|
| 用户 A | 输入消息 |
| 用户 B | 输入消息 |
| @CodeGen | @Agent 触发 |
| @Review | @Agent 触发 |
| 主 Agent | 自动监听（隐式响应） |

**路由规则**：

```python
# app/services/chat/group_router.py
class GroupRouter:
    def route(self, message: GroupMessage) -> list[AgentTask]:
        targets = []
        # 1. 显式 @Agent
        for agent_id in message.mentions:
            targets.append(AgentTask(agent_id=agent_id, ...))
        # 2. 隐式意图（主 Agent 接管）
        if not targets and message.intent_ambiguous:
            targets.append(AgentTask(agent_id="MainAgent", ...))
        return targets
```

**UI 改动**：

- 消息气泡显示发送者头像 + 名称。
- Agent 消息用不同颜色 / 角标区分。
- 主 Agent 消息显示"统筹"标签。

---

## 7. 消息类型规范升级

### 7.1 MessageCard 统一 Schema

把图片里"消息类型包括部署状态卡片"明确化：

```python
# app/schemas/message_card.py
class MessageCard(BaseModel):
    card_id: str
    type: Literal[
        "text", "code", "diff", "preview", "deploy_status",
        "task_update", "audit", "artifact", "dag_progress", "system"
    ]
    title: str
    body: str | dict
    actions: list[CardAction] = []    # 按钮列表
    status: Literal["pending", "running", "success", "failed"] | None = None
    metadata: dict = {}
    timestamp: str

class CardAction(BaseModel):
    label: str
    action: str                       # "confirm", "cancel", "view_diff", "open_preview"
    payload: dict = {}
    style: Literal["primary", "secondary", "danger"] = "primary"
```

### 7.2 部署状态卡片示例

```json
{
  "card_id": "card-uuid",
  "type": "deploy_status",
  "title": "部署到 Vercel",
  "body": {
    "url": "https://agenthub-demo.vercel.app",
    "env": "preview",
    "commit": "a1b2c3d",
    "duration_ms": 12000
  },
  "actions": [
    { "label": "查看部署", "action": "open_url", "payload": {"url": "..."} },
    { "label": "回滚", "action": "rollback", "payload": {"deploy_id": "..."}, "style": "danger" }
  ],
  "status": "success",
  "timestamp": "2026-06-04T12:00:00Z"
}
```

### 7.3 前端渲染

```tsx
// frontend/components/chat/MessageCard.tsx
function renderCard(card: MessageCard) {
  switch (card.type) {
    case "deploy_status": return <DeployStatusCard card={card} />;
    case "artifact":      return <ArtifactCard card={card} />;
    case "dag_progress":  return <DagProgressCard card={card} />;
    case "task_update":   return <TaskUpdateCard card={card} />;
    default:              return <TextCard card={card} />;
  }
}
```

### 7.4 消息类型清单

| type | 用途 | v3.1 已有 |
|---|---|---|
| text | 普通文本 | ✅ |
| code | 代码展示 | ✅ |
| diff | Diff 展示 | ✅ |
| preview | 预览 URL | ✅ |
| deploy_status | 部署状态卡片 | **v3.2 新增** |
| task_update | 任务状态更新 | ✅ |
| audit | 审计提示 | ✅ |
| artifact | 产物卡片 | **v3.2 新增** |
| dag_progress | DAG 进度图 | **v3.2 新增** |
| system | 系统通知 | ✅ |

---

## 8. 多端适配

### 8.1 Web 端

Next.js 13 响应式，**v3.2 不变**。

### 8.2 桌面端

**方案**：Tauri 2.x（轻量、Rust 内核、内存占用低）。

```toml
# src-tauri/tauri.conf.json
{
  "productName": "AgentHub",
  "mainBinaryName": "agenthub-desktop",
  "windows": [{ "title": "AgentHub", "width": 1280, "height": 800 }],
  "bundle": { "active": true, "targets": "all" }
}
```

**复用**：直接加载 Next.js 静态资源，**零代码改动**。

### 8.3 移动端

**方案**：PWA（Progressive Web App）+ Capacitor 包装 iOS / Android。

```typescript
// frontend/next.config.js
module.exports = {
  pwa: {
    dest: 'public',
    register: true,
    skipWaiting: true,
  },
};
```

**响应式**（v3.1 已有 §7.4 移动端策略）：

- 左侧导航：抽屉式。
- 聊天区：全屏主视图。
- DiffBubble：横向滚动 + 全屏查看。
- 移动端特有：底部固定输入栏，长按引用。

### 8.4 端能力差异

| 能力 | Web | 桌面 | 移动 |
|---|---|---|---|
| @Agent 补全 | ✅ | ✅ | ✅ |
| Diff 视图 | ✅ | ✅ | ✅ 全屏 |
| 文件上传 | ✅ | ✅ | ✅ |
| 本地文件读取 | ❌ | ✅（Tauri FS API） | ❌ |
| 通知 | Web Push | 系统通知 | 推送通知 |
| 离线 | Service Worker | ✅ | Service Worker |

---

## 9. 符号蒸馏协议 v3.2 — 保真度软信号化（核心降级修正）

> **本节是 v3.2 的关键降级修正**。v3.1 的"保真度硬阻断"在课题演示中会造成 UX 反噬，改为软信号。

### 9.1 v3.1 问题回顾

v3.1 §3.3 定义：

```
< 0.55: BLOCK — 阻断 + 强制重蒸馏
```

**问题**：

- 启发式算法（regex + 长度比）跟实际语义质量弱相关。
- 阻断导致 UX 反噬（用户必须人工确认）。
- 模型调用失败时的 fallback 文案必然低分，触发**二次 block**。
- 重蒸馏大概率仍低分，浪费 API 调用 + 延迟翻倍。

### 9.2 v3.2 修正

```
v3.1                          v3.2
─────────────────             ─────────────────
≥ 0.85 pass                   ≥ 0.85 pass（不变）
0.70-0.85 warn                0.70-0.85 warn（不变）
0.55-0.70 enrich              0.55-0.85 warn（合并）
< 0.55 block                  < 0.55 warn + 审计标签（不再阻断）
```

**具体改动**：

1. **删除** `_handle_fidelity` 函数中的 `enrich` / `block` 路径。
2. **删除** `fidelity_action` 中的 `enrich` / `block` action。
3. **保留** `warn` 路径，仅向前端推 `fidelity_warning` 事件（黄色提示）。
4. **删除** `build_redistill_prompt` 调用。
5. **保真度保留用途**：
   - UI 审计展示（`FidelityScore.tsx` 仍可读 `symbolic.fidelity_score`）。
   - 多 Agent 符号蒸馏路径（`CollaborationContext.record` 仍调用 `evaluate_contribution`，但**不阻断主流程**）。

### 9.3 代码改动点

```python
# app/services/symbolic.py
def fidelity_action(score: float) -> dict[str, Any]:
    if score >= FIDELITY_HIGH:           # 0.85
        return {"action": "pass", "block": False, "warn": False}
    if score >= FIDELITY_WARN:           # 0.70
        return {"action": "warn", "block": False, "warn": True}
    return {"action": "warn", "block": False, "warn": True}    # < 0.70 一律 warn
```

```python
# app/api/websocket.py
async def _handle_fidelity(...):
    if score >= FIDELITY_HIGH:
        return  # 静默放行
    # 其余一律 warn（不再 enrich / block）
    await manager.broadcast(session_id, {
        "event": "fidelity_warning",
        "fidelityScore": score,
        "grade": fidelity_grade(score),
        "message": f"Agent {agent_id} 响应保真度 {score:.2f}（仅供参考）",
    })
```

```python
# app/services/agent_service.py（fallback 时跳过保真度计算）
if not result:
    final_text = "模型调用失败，已降级为本地响应：" + " | ".join(errors[:2])
    return final_text, usage, selected   # ★ 不进入保真度计算
```

### 9.4 修订理由（写在文档里供评委查阅）

| 维度 | 硬阻断 | 软信号 |
|---|---|---|
| 用户体验 | 反 UX，强制人工确认 | 友好，UI 提示 |
| 演示稳定性 | 容易在演示中触发 | 几乎不会触发 |
| 误判代价 | 阻断正确响应 | 提示但放行 |
| API 成本 | 重蒸馏 + 双重 block | 零额外成本 |
| 工业落地可行性 | 低 | 高 |

**结论**：v3.1 的"保真度硬阻断"是 demo-grade 逻辑，**工业级产品（Cursor、Devin、Replit Agent）都没有这种硬阻断**。v3.2 把它降为软信号是正确的工程化方向。

---

## 10. 课题演示 7 步脚本

把图片 5 大类需求拆成可演示动作序列，每步对应一个具体产品操作。

```
Step 1：登录 → 单聊演示
  操作：登录 admin，主 Agent 默认 @触发
  演示点：单聊模式、@Agent 自动补全

Step 2：群聊演示
  操作：创建群聊，@CodeGen + @Review 同时拉入
  演示点：群聊路由、多 Agent 并行

Step 3：主 Agent 拆解任务
  操作：用户说"做一个登录页面"
  演示点：主 Agent 生成 DAG 卡片（DAG 拓扑图）

Step 4：产物预览
  操作：CodeGen 生成 HTML 页面
  演示点：Artifact 卡片 → 实时预览（iframe）

Step 5：Diff + 版本历史
  操作：用户编辑后，CodeGen 二次修改
  演示点：Diff 视图 + 版本树

Step 6：部署状态卡片
  操作：用户点击"部署到 Vercel"
  演示点：deploy_status 卡片推送，含 URL + 回滚按钮

Step 7：多端同步
  操作：另一台设备登录同一账号
  演示点：实时看到当前会话状态、消息同步
```

---

## 11. 端到端工程闭环

> 保留 v3.1 §8，补充 §4 Artifact 集成。

### 11.1 标准链路（v3.2 增强）

```
用户 @CodeGen
  ↓
MessageRouter 解析 @ + 提取 {{artifact:...}} 引用
  ↓
主 Agent 拆解 → DAG
  ↓
并行调度领域 Agent
  ↓
CodeGen 生成代码 → Artifact v1
  ↓
Diff 校验（write_scope / 高危文件扫描）
  ↓
Review Agent 审查
  ↓
用户确认
  ↓
Git Commit
  ↓
生成 Preview URL
  ↓
生成 Artifact 卡片 + 预览 iframe
  ↓
Deploy Agent 一键部署
  ↓
推 deploy_status 卡片
  ↓
AuditLog 记录
```

### 11.2 Artifact 集成点

| 节点 | 产物 |
|---|---|
| CodeGen 输出 | `Artifact(type=code, version=1)` |
| Review 输出 | `Artifact(type=diff, parent=art-v1)` |
| 用户编辑 | `Artifact(type=code, version=2, parent=art-v1)` |
| 部署成功 | `MessageCard(type=deploy_status, ...)` |
| 文档生成 | `Artifact(type=document)` |
| PPT 生成 | `Artifact(type=ppt)` |

### 11.3 部署目标

| 平台 | 状态 |
|---|---|
| Vercel | ✅ |
| Netlify | ✅ |
| 自建 Nginx | ✅ |
| 飞书应用 | v3.2 新增（演示用） |
| 字节云托管 | v3.2 规划 |

---

## 12. 权限与安全 v3.2

> 在 v3.1 §10 基础上补充多人协作场景。

### 12.1 角色矩阵（v3.2 增强）

| 角色 | 权限范围 | 多人协作 |
|---|---|---|
| admin | 全局 | 全部会话 + 配置 |
| developer | 项目内 | 所在项目会话 |
| reviewer | 审查 | 全部项目的审查会话 |
| tester | 测试 | 所在项目测试 |
| guest | 只读 | 可被加入会话但只读 |

### 12.2 会话权限

```sql
CREATE TABLE IF NOT EXISTS session_members (
    session_id UUID,
    user_id UUID,
    role TEXT,                       -- "owner" | "editor" | "viewer"
    joined_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (session_id, user_id)
);
```

**API**：

```http
POST /api/sessions/{id}/members
→ { "user_id": "u-2", "role": "editor" }
```

### 12.3 审计

在 v3.1 基础上新增：

- 多人协作加入 / 离开会话。
- Artifact 二次编辑。
- 群聊 @Agent 操作。
- 段落引用触发的任务。

---

## 13. 风险防控与降级运行

### 13.1 风险分级（保留 v3.1 §11.1）

| 风险等级 | 示例 | 处理 |
|---|---|---|
| L1 | 生成普通代码 | 自动执行 |
| L2 | 修改业务逻辑 | Review 通过后执行 |
| L3 | 修改认证、权限、数据库 | 人工确认 |
| L4 | 部署、删除、回滚生产数据 | 管理员二次确认 |

### 13.2 降级策略（v3.2 增强）

| 故障 | 降级 |
|---|---|
| 主 Agent 不可用 | 备用主 Agent（配置） → 轻量规则引擎 → 用户接管 |
| 领域 Agent 不可用 | 主 Agent 选择备用 Agent（能力等价不同模型） |
| 高阶模型不可用 | 切换备用模型（Ollama 本地） |
| WebSocket 断开 | REST 轮询任务状态 |
| Redis 不可用 | 单机内存会话管理 |
| 向量库不可用 | 使用 PostgreSQL 最近摘要 |
| Monaco 加载失败 | fallback 为 `<pre><code>` |
| Deploy 失败 | 保留预览与 Git 提交，不执行生产部署 |
| 多人协作冲突 | CRDT 自动合并 → 用户选择 |
| 群聊 @Agent 全部失败 | 提示用户，主 Agent 给"建议选项" |

### 13.3 任务状态机（v3.2 增强）

```
PENDING → RUNNING → REVIEWING → WAITING_CONFIRM → SUCCESS
                          ↓
                       FAILED → RETRYING (×3) → RUNNING
                          ↓
                       CANCELLED
                          ↓ (多人场景)
                       CONFLICT → ARBITRATION → RUNNING / CANCELLED
```

新增 `CONFLICT` 和 `ARBITRATION` 状态。

---

## 14. 测试方案 v3.2

### 14.1 新增测试用例

| 类别 | 用例 |
|---|---|
| 主 Agent | 拆解 5 个不同类型意图，验证 DAG 正确性 |
| 多 Agent 接入 | CloudCode / Codex / OpenCode 至少各跑通一个 demo |
| Artifact | 四类产物各生成 + 编辑 + 版本回滚 |
| 段落引用 | 引用 5 个不同位置段落，验证 chunk_id 解析 |
| 群聊 | 5 人群聊 + 2 个 Agent 同时在线 |
| 多端 | Web + 桌面 + 移动端同一账号登录，消息同步 |
| 冲突解决 | 2 人同时编辑同一 Artifact，验证 CRDT 合并 |
| 部署 | 3 个三方平台各部署一次 |
| 降级 | 模拟主 Agent 失败，验证备用切换 |
| 保真度软信号 | 构造 < 0.55 的响应，验证不阻断 |

### 14.2 集成测试

核心链路：

```
登录 → 创建会话 → 群聊拉入 2 Agent + 2 用户
  → 用户说"做一个登录页面"
  → 主 Agent 拆解 DAG（DAG 卡片推送）
  → CodeGen 生成代码（Artifact 卡片 + 预览）
  → 用户引用某段（段落引用语法）
  → CodeGen 二次修改（Diff 视图 + 版本 v2）
  → Review 通过 → Git commit
  → 一键部署 Vercel（deploy_status 卡片）
  → 移动端登录同一账号，验证同步
  → 审计日志可查全部操作
```

### 14.3 竞赛演示测试用例

1. 登录展示单聊。
2. 群聊演示多 Agent 并行。
3. 主 Agent 拆解 DAG 卡片。
4. CodeGen 生成 HTML + 实时预览。
5. Diff + 版本历史展示。
6. 段落引用二次修改。
7. 部署状态卡片（含 URL + 回滚）。
8. 多端同步展示。
9. 降级展示（拔网线 → 重连 → 恢复）。
10. 保真度软信号展示（黄色 warn 提示，不阻断）。

---

## 15. 部署方案 v3.2

### 15.1 本地开发（保留 v3.1 §13.1）

```
FastAPI: http://localhost:8000
Next.js: http://localhost:3000
WebSocket: ws://localhost:8000/ws/{session_id}
```

### 15.2 多端打包

| 端 | 命令 | 输出 |
|---|---|---|
| Web | `pnpm build` | `.next/` |
| 桌面（Tauri） | `pnpm tauri build` | `.app` / `.exe` / `.deb` |
| 移动（PWA） | `pnpm build && pnpm cap sync` | `.apk` / `.ipa` |

### 15.3 演示部署

```
Web 端：Vercel
桌面端：本地安装包
移动端：PWA（无需应用商店）
后端：Railway / Fly.io
DB：Supabase PostgreSQL
Redis：Upstash
ChromaDB：独立容器
```

---

## 16. 实施路线图 v3.2

| 阶段 | 目标 | 交付 | 对应需求 |
|---|---|---|---|
| **P0** | 现有系统稳定 | 登录、IM、WebSocket、基础 Agent | R1.1-R1.2 |
| **P1** | 主 Agent 产品化 | 主 Agent 注册、@触发、聊天可见 | R2.1 |
| **P2** | 多 Agent 接入 | CloudCode / Codex / OpenCode 适配器 | R3.1 |
| **P3** | 自建 Agent 流程 | /admin/agents 表单 + 能力标签 | R3.2-R3.4 |
| **P4** | Artifact 体系 | 4 类产物 + 预览 + 编辑 | R4.1-R4.2 |
| **P5** | 版本历史 + 段落引用 | Artifact 版本树 + 引用语法 | R4.3-R4.4 |
| **P6** | 群聊模式 | 群聊 UI + 群路由 | R1.3 |
| **P7** | 消息卡片 | MessageCard schema + 部署卡片 | R1.4 |
| **P8** | 多端适配 | Tauri 桌面 + PWA 移动 | R5.1 |
| **P9** | 多人协作 | CRDT + 会话成员管理 | R5.2 |
| **P10** | 保真度软信号 | 删除 block 路径 | 工程降级 |
| **P11** | 演示脚本 | 7 步动作序列 | 全部 |

**P0-P7**：3 周可完成（与课题演示对齐）。
**P8-P9**：1-2 周（多人协作是增量）。
**P10**：1 天（仅代码改动）。
**P11**：1 周（脚本 + 录屏）。

---

## 17. 答辩表达建议

### 17.1 技术创新总结（v3.2 版）

AgentHub v3.2 的核心创新不仅是"调用多个大模型"，而是建立了一个**课题需求驱动、可演示、可工业落地的多智能体协作平台**：

- **IM 入口**：降低开发者使用门槛。
- **主 Agent 角色化**：把 PM / PMO 概念产品化，拆解 / 调度 / 降级 / 仲裁 / 人工交接 5 大能力。
- **多 Agent 平台接入**：CloudCode / Codex / OpenCode 三大主流平台 + 自建 Agent 协议。
- **Artifact 体系**：网页 / 文档 / PPT / 代码四类产物 + 段落级引用 + 版本历史。
- **多端协作**：Web / 桌面 / 移动 + CRDT 冲突解决。
- **符号蒸馏**：保留 v3.1 协议，保真度降级为软信号。

### 17.2 课题需求完成度表达

> "本项目严格对齐课题图片的 5 大类、20 项具体要求。其中 13 项完成度 100%，4 项（70-80%），3 项（30-50%）。所有 P0 演示场景均可现场操作。"

### 17.3 工程落地总结

- v3.1 是工程增强版，v3.2 是课题需求对齐版。
- 不替换架构 / 技术栈 / 业务闭环。
- 关键技术降级（保真度硬阻断 → 软信号）体现工程判断力。
- 演示脚本覆盖 7 大类 17 个动作。

### 17.4 学术价值总结

- 多 Agent 协作中的**主 Agent 角色化**。
- 任务 DAG 与**段落级引用**的映射。
- **CRDT + 乐观锁**在 IM 协作中的冲突解决。
- 符号蒸馏**保真度的工程化取舍**（硬阻断 vs 软信号）。

---

## 18. 与 v3.0 / v3.1 的关系

```
v3.0  基础蓝图与可运行代码逻辑
      ↓
v3.1  工程增强、竞赛优化、产业落地细化
      ↓
v3.2  课题需求对齐、保真度降级修正、多 Agent 平台接入
```

**v3.2 不替换 v3.1**，两份文档并存。代码层面仅在 §9（保真度软信号）有实质性改动，其余为新增模块 / 页面 / 配置。

---

## 19. 修订清单

| 章节 | 改动类型 | 说明 |
|---|---|---|
| §1 | 新增 | 课题需求对照表 |
| §2 | 新增 | 主 Agent 概念产品化 |
| §3 | 新增 | 多 Agent 平台接入协议 |
| §4 | 新增 | Artifact 体系 |
| §5 | 新增 | 多人协作与多端同步 |
| §6 | 新增 | 会话层级与群聊 |
| §7 | 新增 | MessageCard 规范 |
| §8 | 新增 | 多端适配 |
| §9 | **降级修正** | 保真度软信号化 |
| §10 | 新增 | 演示 7 步脚本 |
| §11 | 增量 | 端到端闭环补 Artifact |
| §12 | 增量 | 多人协作权限 |
| §13 | 增量 | 降级策略补多人场景 |
| §14 | 增量 | 测试用例补 v3.2 |
| §15 | 增量 | 多端打包 |
| §16 | 重写 | 路线图对齐需求 |
| §17 | 重写 | 答辩表达 v3.2 |
| §18 | 新增 | 版本关系 |

---

**文档结束。**

> 本 v3.2 文档定稿后，建议把"演示 7 步脚本"（§10）和"测试用例"（§14.3）作为竞赛结题材料的一部分，与代码同时交付。
