# 什么是 AgentHub？

> 文档状态：产品介绍（迁移中）。功能勾选不代表生产级完成度；请参阅
> [当前架构总览](./architecture.md) 和 [文档治理](../../governance/documentation-standard.md)。

AgentHub 是一个**企业级自托管多智能体协作平台**，旨在让团队通过自然语言编排多个 AI Agent 完成复杂任务。

## 设计理念

### 从单 Agent 到 Agent 团队

传统的 AI 助手是一对一对话。AgentHub 扩展了这个模型：

- **主 Agent (PM)**: 负责任务拆解、调度、降级、仲裁
- **领域 Agent**: 专精于特定能力（代码审查、文档写作、数据分析等）
- **AgentNet 协作网络**: Agent 之间可以互相发现、协商、委托任务

### 去中心化 vs 中心化

不同于传统的中心化工作流引擎，AgentHub 的 AgentNet 采用**去中心化 DAG 架构**：

```
传统工作流:         AgentNet:
  Orchestrator        ┌─→ Agent A ─→ Agent D ─┐
  → Agent A           │                        │
  → Agent B           PM ─→ Agent B ─→ Agent E ─→ 结果
  → Agent C           │                        │
                      └─→ Agent C ─→ Agent F ─┘
```

### 4 层记忆引擎 (ContextOS)

```
L0 工作记忆 → Redis (24h TTL)
L1 短期摘要 → PostgreSQL (7d 滑动窗口)
L2 向量记忆 → Qdrant (语义检索)
L3 知识图谱 → PostgreSQL (实体关系图)
```

## 竞品对比

| 能力 | AgentHub | Dify | CrewAI | n8n |
|------|----------|------|--------|-----|
| 多 Agent 协作 | ✅ DAG + 涌现通信 | ❌ 单一 Agent | ⚠️ 顺序执行 | ❌ |
| Rust 性能核心 | ✅ P95 < 80ms | ❌ | ❌ | ❌ |
| 4 层记忆 | ✅ ContextOS | ❌ | ❌ | ❌ |
| MCP 协议 | ✅ STDIO + SSE | ⚠️ 基础 | ❌ | ❌ |
| A2A 协议 | ✅ | ❌ | ❌ | ❌ |
| 企业级 IAM+KMS | ✅ | ⚠️ RBAC | ❌ | ❌ |
| Docker 沙箱 | ✅ seccomp | ❌ | ❌ | ❌ |
| 开源协议 | Apache 2.0 | Apache 2.0 | MIT | Sustainable Use |
