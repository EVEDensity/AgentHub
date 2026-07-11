# AgentHub 文档

欢迎来到 **AgentHub** — 企业级自托管多智能体协作平台。

## 什么是 AgentHub？

AgentHub 是一个开源的企业级多智能体 (Multi-Agent) 协作平台，提供从单 Agent 到 Agent 团队的完整解决方案。

### 核心能力

- **AgentNet 协作网络** — DAG 拓扑编排，去中心化多 Agent 协作
- **ContextOS 记忆引擎** — 4 层记忆体系（L0-L3），支持 LLM 自主记忆策略
- **MCP Gateway** — Model Context Protocol 完整实现，STDIO + SSE 双传输
- **RAG 文档检索** — Qdrant + OpenSearch 混合检索，7 步 DeepSearch 流程
- **A2A 互操作** — Agent-to-Agent 开放标准，跨平台 Agent 互通
- **Docker 安全沙箱** — seccomp + AppArmor + 只读文件系统的安全执行环境
- **企业级安全** — IAM + ABAC + KMS + 审计日志，OWASP Top 10 完整防护

### 技术栈

| 层级 | 技术 | 组件 |
|------|------|------|
| 接入层 | Go | Gateway, MCP Gateway, A2A Handler |
| 编排层 | Go + Rust | Orchestrator, AgentNet |
| 性能层 | Rust | stream-core, retrieval-core, fanout-core |
| 离线层 | Python | Knowledge, Embedding, Model Adapter |
| 前端 | Next.js 13 | Warm Studio 2.0 Design System |
| 数据层 | PostgreSQL, Qdrant, OpenSearch, Redis, NATS | 持久化 + 向量 + 全文 + 缓存 + 事件 |

### 开源

AgentHub 基于 [Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0) 协议开源。
