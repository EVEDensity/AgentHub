# Agent API

Agent CRUD (创建、读取、更新、删除) 操作的完整 API 参考。

## 端点总览

| Method | Path | 说明 |
|--------|------|------|
| `GET` | `/api/agents` | 列出所有 Agent |
| `POST` | `/api/agents` | 创建 Agent |
| `GET` | `/api/agents/:id` | 获取 Agent 详情 |
| `PUT` | `/api/agents/:id` | 更新 Agent |
| `DELETE` | `/api/agents/:id` | 删除 Agent |
| `POST` | `/api/agents/:id/test` | 测试 Agent |

## 创建 Agent

```bash
POST /api/agents
Content-Type: application/json
Authorization: Bearer YOUR_API_KEY

{
  "name": "CodeReviewBot",
  "model": "claude-sonnet-4-6",
  "systemPrompt": "You are a senior code reviewer.",
  "capabilities": ["code_review", "diff_analysis"],
  "tools": ["code_review", "security_scan"],
  "temperature": 0.3,
  "maxTokens": 4096,
  "tags": ["code", "review"]
}
```

### 响应

```json
{
  "id": "agent-42",
  "name": "CodeReviewBot",
  "model": "claude-sonnet-4-6",
  "systemPrompt": "You are a senior code reviewer.",
  "capabilities": ["code_review", "diff_analysis"],
  "status": "ready",
  "createdAt": "2026-07-04T10:00:00Z",
  "updatedAt": "2026-07-04T10:00:00Z"
}
```

## 查询 Agent

### 列表查询 (支持过滤)

```bash
GET /api/agents?capability=code_review&status=ready&page=1&limit=20
```

### 单个查询

```bash
GET /api/agents/agent-42
```

## 更新 Agent

```bash
PUT /api/agents/agent-42
{
  "systemPrompt": "You are an expert code reviewer with 20 years experience.",
  "temperature": 0.2
}
```

## 删除 Agent

```bash
DELETE /api/agents/agent-42
```

## Agent 配置字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | ✅ | Agent 显示名称 |
| `model` | string | ✅ | LLM 模型标识 |
| `systemPrompt` | string | ✅ | 系统提示词 |
| `capabilities` | string[] | — | 能力标签列表 |
| `tools` | string[] | — | 关联工具列表 |
| `temperature` | number | — | 温度参数 (0-2) |
| `maxTokens` | number | — | 最大输出 token |
| `tags` | string[] | — | 自定义标签 |
| `avatar` | string | — | 头像 URL |

## 下一步

- [工作流 API](/zh/api/workflow) — 编排多个 Agent
- [核心概念](/zh/guide/concepts) — Agent 能力标签详解
