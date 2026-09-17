# AgentNet DAG 编排

> 兼容说明：AgentNet/DAG 是现有 Legacy 控制面。新任务必须通过 Mission
> 和 WorkUnit 建模，AgentNet 不得新增第二套业务真相。

AgentNet 的去中心化 DAG 编排引擎详解。

## 动态 DAG 拓扑

与传统工作流不同，AgentNet 的 DAG 是动态的 — 节点和边可以在运行时被 Agent 自动添加或移除。

```
初始 DAG:          执行中:           完成:
                    
  [S]               [S]               [S]
  / \               / \               / \
 [A] [B]     →    [A] [B]     →     [A] [B]
  \ /               \ / \             \ / \
  [C]              [C] [D*]          [C] [D*]
                    \ /               \ /
                    [E]               [E]

*D 由 Agent A 在运行时动态创建 (Agent Spawn Agent)
```

## 就绪检测 (BFS)

任务分派采用 BFS 检测就绪节点：

```go
func getReadyNodes(dag *DAG) []*Node {
    var ready []*Node
    for _, node := range dag.Nodes {
        if node.Status == "pending" && allDepsCompleted(node) {
            ready = append(ready, node)
        }
    }
    return ready
}
```

关键特性：
- **最大并行度**: 所有依赖已满足的节点同时执行
- **动态重新调度**: 节点失败后自动寻找等价备用 Agent
- **循环检测**: 添加边时自动检测并拒绝形成环路

## 四种调度策略

| 策略 | 算法 | 适用场景 |
|------|------|---------|
| `round-robin` | 轮流分配 | 能力同质的 Agent 池 |
| `least-loaded` | 当前负载最低的 Agent | 高并发场景 |
| `capability-match` | 能力标签交集最大 | 跨领域多 Agent 协作 |
| `cost-optimized` | 成本函数最小化 | 预算敏感场景 |

## 运行时操作

### 添加节点

```bash
POST /api/agentnet/dag/{dagId}/nodes
{
  "id": "node-fixer",
  "type": "agent",
  "agent": "BugFixBot",
  "capabilities": ["code_fix"],
  "dependencies": ["node-reviewer"]
}
```

### 添加边

```bash
POST /api/agentnet/dag/{dagId}/edges
{
  "from": "node-reviewer",
  "to": "node-fixer",
  "label": "发现 Bug"
}
```

### 移除节点

```bash
DELETE /api/agentnet/dag/{dagId}/nodes/node-fixer
```

## 共享记忆通道

AgentNet 中的 Agent 通过 NATS 发布-订阅模式共享记忆：

```
Topic: agentnet.memory.shared.{workspace_id}

Agent A → 发布: "在 auth/login.ts 发现 SQL 注入风险"
    │
    └─→ Agent B (订阅) → 自动调整安全审查优先级
    └─→ Agent C (订阅) → 关联历史相似漏洞记录
```

这种机制实现了"涌现通信" — Agent 无需显式编排即可交换关键信息。

## 性能指标 (Rust agentnet-core)

| 操作 | 延迟 | 对比 (Python) |
|------|------|--------------|
| DAG 节点就绪检测 | < 0.5ms | 5-10ms |
| 边添加 (含环检测) | < 0.2ms | 3-5ms |
| 最佳 Agent 匹配 | < 1ms | 15-30ms |
| 共享记忆发布 | < 0.1ms | 2-5ms |

## 下一步

- [AgentNet 使用教程](/zh/guide/agentnet)
- [A2A 互操作性](/zh/advanced/a2a-interop)
