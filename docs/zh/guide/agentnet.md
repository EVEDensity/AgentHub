# AgentNet 多 Agent 协作

AgentNet 是 AgentHub 的去中心化多智能体协作网络。它支持 **DAG 拓扑编排**、**Agent 自主生成 Agent**、以及 **涌现通信**。

## 核心概念

### DAG 拓扑

工作流中的节点形成有向无环图 (DAG)，AgentNet 负责：
- **任务分派**：根据能力/负载/成本选择最优 Agent
- **并行调度**：BFS 检测就绪节点，最大化并行度
- **动态调整**：运行时添加/移除节点和边

### 四种调度策略

| 策略 | 算法 | 适用场景 |
|------|------|---------|
| `round-robin` | 轮流分配 | 任务同质，Agent 无差别 |
| `least-loaded` | 最少负载优先 | 高并发场景 |
| `capability-match` | 能力标签匹配 | 多领域 Agent 协作 |
| `cost-optimized` | 成本最优 | 预算敏感场景 |

### Agent Spawn Agent

AgentNet 中的 Agent 可以在运行时动态创建子 Agent：

```
主 Agent (PM)
  ├ spawn → CodeGen Agent (生成代码)
  ├ spawn → Review Agent (审查代码)  
  └ spawn → Test Agent (执行测试)
```

子 Agent 的生命周期：`created → running → completed → destroyed`，TTL 默认 10 分钟。

### 涌现通信

当多个 Agent 的共享记忆通道出现模式匹配时，触发涌现通信 — Agent 之间自动交换信息，无需显式编排。

## 使用示例

### 通过聊天触发

```
@主Agent 开发一个用户认证模块，包括登录、注册和密码重置功能
```

主 Agent 会自动拆解任务并分配给不同的领域 Agent。

### 通过 API 调度

```bash
POST /api/agentNet/dispatch
{
  "objective": "Review the auth module for security vulnerabilities",
  "strategy": "capability-match",
  "context": {
    "files": ["auth/login.ts", "auth/session.ts"]
  },
  "timeout": 300000
}
```

### 监听协作事件

```typescript
// 通过 WebSocket 实时监听
const ws = new WebSocket('wss://localhost:8080/ws/agentnet');

ws.onmessage = (event) => {
  const { type, agent, content } = JSON.parse(event.data);
  switch (type) {
    case 'agent.dispatched': console.log(`${agent} 已分配任务`);
    case 'agent.completed':  console.log(`${agent} 已完成`);
    case 'emergence':        console.log('涌现通信触发！');
  }
};
```

## 可视化监控

管理后台的 **AgentNet 拓扑** 面板提供实时可视化：
- WebGL 粒子特效渲染的力导向图
- 任务流粒子沿连线传播
- 涌现通信的粒子爆炸效果
- 实时状态颜色编码

## 下一步

- [AgentNet DAG 编排详解](/zh/advanced/agentnet-dag)
- [A2A 互操作性](/zh/advanced/a2a-interop)
