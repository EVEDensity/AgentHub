# 核心概念

> 文档状态：legacy compatibility guide。AgentNet、ContextOS 和 A2A 的现有
> 描述对应历史能力；新开发请以 Mission/WorkUnit 架构和当前测试为准。

理解 AgentHub 的核心概念有助于你更好地使用平台。

## Agent（智能体）

Agent 是 AgentHub 的基本工作单元。每个 Agent 拥有：

- **身份**：名称、头像、系统提示词
- **能力**：`code_generation`、`doc_writing`、`deployment` 等
- **模型**：Claude、GPT、Ollama 本地模型等
- **工具**：代码执行、网络搜索、文件读写等

```bash
# 通过 API 创建 Agent
curl -X POST http://localhost:8081/api/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "CodeBot",
    "model": "claude-sonnet-4-6",
    "systemPrompt": "You are a senior software engineer.",
    "capabilities": ["code_generation", "code_review"]
  }'
```

## 主 Agent (PM)

主 Agent 是 AgentHub 的协调中枢，负责：

| 能力 | 说明 |
|------|------|
| **任务拆解** | 自然语言 → DAG 拓扑任务图 |
| **调度** | 并行/串行/高风险人工确认 |
| **降级** | 主→备用→规则引擎→人工 |
| **仲裁** | 文件冲突/逻辑冲突解决 |
| **人工交接** | 高风险任务移交人工处理 |

## AgentNet

AgentNet 是去中心化多 Agent 协作网络：

- **DAG 编排**：将任务拆解为有向无环图，自动调度子 Agent 并行/串行执行
- **Agent spawn Agent**：Agent 可动态创建子 Agent 分发子任务
- **涌现通信**：通过共享记忆通道实现 Agent 间的自发信息传递

## ContextOS（记忆引擎）

AgentHub 的 4 层记忆体系：

| 层级 | 存储 | TTL | 用途 |
|------|------|-----|------|
| **L0 工作记忆** | Redis | 24h | 当前会话的实时上下文 |
| **L1 短期摘要** | PostgreSQL | 7d | 会话结束后自动生成摘要 |
| **L2 向量记忆** | Qdrant | 永久 | 语义检索的历史记忆 |
| **L3 知识图谱** | PostgreSQL | 永久 | 实体关系结构化存储 |

## 工作流 (Workflow)

可视化工作流编辑器支持 12 种节点类型：

- **Agent** — 调用 Agent 执行任务
- **Code** — 执行代码（Python/JS/Bash）
- **HTTP** — 调用外部 API
- **Knowledge** — 检索知识库
- **Human** — 人工审批节点
- **Condition** — 条件分支
- **Loop** — 循环控制

## MCP (Model Context Protocol)

AgentHub 完整实现了 Anthropic 的 MCP 协议：

- **STDIO 传输**：本地 CLI Agent 通过标准输入/输出接入
- **SSE 传输**：远程 Agent 通过 HTTP Server-Sent Events 接入
- **工具暴露**：AgentHub 的工具/知识库/记忆作为 MCP Resources 暴露

## A2A (Agent-to-Agent)

Google 发布的 Agent 互操作开放标准：

- **Agent Card**：描述 Agent 的能力、端点、认证方式
- **任务通信**：标准化的任务创建、状态查询、结果获取
- **跨平台互通**：AgentHub Agent ↔ 外部 A2A Agent

## 下一步

- [5 分钟快速部署](/zh/guide/quick-start)
- [创建第一个 Agent](/zh/guide/create-agent)
- [架构总览](/zh/guide/architecture)
