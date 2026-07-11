# 工作流 API

工作流的创建、查询、执行和管理的完整 API 参考。

## 端点总览

| Method | Path | 说明 |
|--------|------|------|
| `GET` | `/api/admin/workflows` | 列出所有工作流 |
| `POST` | `/api/admin/workflows` | 创建工作流 |
| `GET` | `/api/admin/workflows/:id` | 获取工作流详情 |
| `PUT` | `/api/admin/workflows/:id` | 更新工作流 |
| `DELETE` | `/api/admin/workflows/:id` | 删除工作流 |
| `POST` | `/api/admin/workflows/:id/execute` | 执行工作流 |
| `POST` | `/api/admin/workflows/:id/default` | 设为默认 |
| `PATCH` | `/api/admin/workflows/:id/active` | 启用/禁用 |

## 创建工作流

```bash
POST /api/admin/workflows
Content-Type: application/json
Authorization: Bearer YOUR_API_KEY

{
  "name": "代码审查流水线",
  "description": "自动代码审查工作流：代码生成 → 审查 → 测试 → 修复",
  "triggerKeywords": ["@review", "审查代码"],
  "nodes": [
    {
      "id": "start",
      "type": "start",
      "name": "开始",
      "description": "接收审查请求"
    },
    {
      "id": "reviewer",
      "type": "agent",
      "name": "代码审查",
      "description": "审查代码质量和安全",
      "agent": "CodeReviewBot",
      "dependencies": ["start"]
    },
    {
      "id": "tester",
      "type": "agent",
      "name": "运行测试",
      "description": "执行测试套件",
      "agent": "TestBot",
      "dependencies": ["start"]
    },
    {
      "id": "end",
      "type": "end",
      "name": "输出报告",
      "dependencies": ["reviewer", "tester"]
    }
  ],
  "isDefault": false
}
```

### 节点类型说明

| type | 说明 | 特有配置字段 |
|------|------|-------------|
| `start` | 工作流入口 | — |
| `agent` | 调用 Agent | `agent`: Agent ID |
| `tool` | 调用工具 | `tool`: 工具名称 |
| `code` | 执行代码 | `codeConfig`: { language, code, timeout } |
| `http` | HTTP 请求 | `httpConfig`: { method, url, headers, body } |
| `knowledge` | 知识检索 | `knowledgeConfig`: { collectionId, query, topK } |
| `human` | 人工审批 | `humanConfig`: { prompt, assignee, timeout } |
| `ifelse` | 条件分支 | `rules`: 条件规则数组 |
| `end` | 工作流终点 | — |

## 执行工作流

```bash
POST /api/admin/workflows/42/execute
{
  "input": {
    "message": "请审查 auth/login.ts",
    "context": { "file": "auth/login.ts" }
  }
}
```

### 响应 (流式)

```json
{
  "executionId": "exec-123",
  "status": "running",
  "stream": "ws://localhost:8080/ws/workflow/exec-123"
}
```

## 下一步

- [构建工作流教程](/zh/guide/build-workflow)
- [AgentNet DAG 编排](/zh/advanced/agentnet-dag)
