# ContextOS 4 层记忆

> 迁移说明：本文描述历史记忆实现。目标架构将其收敛为 Context Compiler
> 的 Memory Layers，并要求 ACL、来源、freshness 和可重放上下文。

ContextOS 是 AgentHub 的四层记忆引擎，为 Agent 提供从即时工作上下文到长期知识图谱的完整记忆体系。

## 记忆分层

```
L0 ─ 工作记忆 (Working Memory)
    Redis · TTL 24h · 低延迟
    当前会话上下文、最近的对话轮次

L1 ─ 短期摘要 (Short-term Summary)
    PostgreSQL · TTL 7d · 结构化
    会话摘要、关键决策、Action Items

L2 ─ 向量记忆 (Vector Memory)
    Qdrant · 永久 · 语义检索
    所有历史交互的向量嵌入，支持语义搜索

L3 ─ 知识图谱 (Knowledge Graph)
    PostgreSQL + NebulaGraph · 永久
    实体关系、因果链、项目知识结构
```

## 记忆流程

```
用户消息
    │
    ▼
L0 检索 (最近 10 轮对话) → 注入上下文
    │
    ▼
L2 语义检索 (相似历史交互) → 注入上下文
    │
    ▼
L3 实体关系查询 (相关概念) → 注入上下文
    │
    ▼
组装完整 Context → 发送给 LLM
```

## 睡眠压缩

当 Agent 会话空闲超过 30 分钟时，ContextOS 自动触发睡眠压缩：

1. **L0 → L1**：工作记忆中的原始对话轮次被 LLM 压缩为结构化摘要
2. **L1 → L2**：过期摘要生成向量嵌入，存入 Qdrant
3. **L2 → L3**：高频实体关系被提取并写入知识图谱

## LLM 自主记忆策略

Agent 可以在系统提示词中指定记忆策略：

```yaml
memory_strategy:
  retention:
    important_decisions: permanent   # 重要决策永久保留
    casual_chat: 24h                 # 闲聊 24h 后丢弃
    code_review: 90d                 # 代码审查保留 90 天
  retrieval:
    strategy: hybrid                 # 语义 + 关键词混合
    max_tokens: 2000                 # 记忆注入上限
    recency_weight: 0.3              # 时间衰减权重
    relevance_threshold: 0.65        # 最低相关度阈值
```

## 检索参数调优

| 参数 | 默认值 | 影响 |
|------|--------|------|
| `recency_weight` | 0.3 | 提高后最近记忆权重更大 |
| `relevance_threshold` | 0.65 | 提高后检索结果更精确但更少 |
| `max_tokens` | 2000 | 注入 LLM 的最大记忆 token 数 |
| `compression_trigger` | 30min | 空闲多久后触发睡眠压缩 |

## 下一步

- [性能调优](/zh/advanced/performance) — 降低检索延迟
- [架构详解](/zh/guide/architecture) — 完整架构图
