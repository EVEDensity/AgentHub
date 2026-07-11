# WebSocket 事件

AgentHub 通过 WebSocket 提供实时事件流，支持 Agent 状态、工作流执行、AgentNet 协作和系统事件的推送。

## 连接

```javascript
const ws = new WebSocket('wss://localhost:8080/ws');

ws.onopen = () => {
  // 订阅特定事件类型
  ws.send(JSON.stringify({
    type: 'subscribe',
    channels: ['agent', 'workflow', 'agentnet']
  }));
};
```

## 事件频道

| 频道 | 说明 | 事件类型 |
|------|------|---------|
| `agent` | Agent 生命周期事件 | `agent.thinking`, `agent.output`, `agent.completed`, `agent.error` |
| `workflow` | 工作流执行事件 | `workflow.started`, `workflow.node.completed`, `workflow.completed` |
| `agentnet` | AgentNet 协作事件 | `agentnet.dispatched`, `agentnet.emergence`, `agentnet.dag.progress` |
| `chat` | 聊天消息事件 | `chat.message`, `chat.streaming`, `chat.artifact` |
| `system` | 系统状态事件 | `system.status`, `system.notice` |

## Agent 事件

```json
// agent.thinking — Agent 开始思考
{
  "type": "agent.thinking",
  "agentId": "agent-42",
  "agentName": "CodeReviewBot",
  "timestamp": "2026-07-04T10:00:00Z"
}

// agent.output — Agent 输出内容
{
  "type": "agent.output",
  "agentId": "agent-42",
  "content": "发现安全漏洞：SQL 注入风险在第 45 行...",
  "chunk": 3,
  "isFinal": false
}

// agent.completed — Agent 任务完成
{
  "type": "agent.completed",
  "agentId": "agent-42",
  "duration": 2340,
  "tokensUsed": 1523,
  "output": "完整审查报告..."
}
```

## AgentNet 事件

```json
// agentnet.emergence — 涌现通信触发
{
  "type": "agentnet.emergence",
  "agents": ["agent-42", "agent-89"],
  "channel": "shared_memory",
  "pattern": "安全漏洞检测",
  "timestamp": "2026-07-04T10:05:00Z"
}
```

## 重连策略

WebSocket 断开时自动重连：

```javascript
let retries = 0;
const maxRetries = 5;

function connect() {
  const ws = new WebSocket('wss://localhost:8080/ws');
  ws.onclose = () => {
    if (retries < maxRetries) {
      const delay = Math.min(1000 * Math.pow(2, retries), 30000);
      setTimeout(connect, delay);
      retries++;
    }
  };
  return ws;
}
```

## 降级方案

当 WebSocket 不可用时，自动降级为 REST 轮询：

```bash
GET /api/events/poll?since=1712345678&channels=agent,workflow
```

## 下一步

- [Agent API](/zh/api/agent) — REST API 操作
- [A2A Protocol API](/zh/api/a2a) — Agent 间通信
