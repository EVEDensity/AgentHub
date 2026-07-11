# API 概述

AgentHub 提供 RESTful API 和 WebSocket 双向通信。所有 API 基于 Go Gateway 统一入口。

## 基础信息

- **Base URL**：`http://localhost:8081`
- **Content-Type**：`application/json`
- **认证方式**：JWT Bearer Token（`Authorization: Bearer <token>`）
- **管理后台**：`http://localhost:3000/admin`
- **Grafana**：`http://localhost:3001` (admin/admin)
- **默认账号**：`admin@agenthub.dev / agenthub123`

## API 分组

### Agent API
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/agents` | 列出所有 Agent |
| POST | `/api/agents` | 创建 Agent |
| GET | `/api/agents/:id` | 获取 Agent 详情 |
| PUT | `/api/agents/:id` | 更新 Agent |
| DELETE | `/api/agents/:id` | 删除 Agent |
| GET | `/platform/agent-versions/:agentId` | Agent 版本历史 |

### 聊天 API
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/chat` | 发送消息 |
| WS | `/ws` | WebSocket 实时通信 |
| POST | `/v1/public/chat` | 公开 API 端点 |

### 工作流 API
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/workflows` | 列出工作流 |
| POST | `/api/workflows` | 创建工作流 |
| GET | `/api/workflows/:id` | 获取工作流详情 |
| POST | `/api/workflows/:id/run` | 执行工作流 |

### 知识库 API
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/knowledge/search` | RAG 文档检索 |
| POST | `/api/knowledge/documents` | 上传文档 |
| DELETE | `/api/knowledge/documents/:id` | 删除文档 |
| GET | `/platform/knowledge/rag-search` | 混合检索 (Qdrant+OpenSearch) |

### AgentNet API
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/agentnet/dispatch` | 分发任务到 AgentNet |
| GET | `/agentnet/dag/:id` | 查看 DAG 状态 |
| GET | `/agentnet/topology` | 查看拓扑结构 |
| WS | `/agentnet/stream/:id` | 流式监听任务事件 |

### MCP Gateway API
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/platform/mcp/tools` | 列出 MCP 工具 |
| POST | `/platform/mcp/tools/:name/call` | 调用 MCP 工具 |
| GET | `/platform/mcp/resources` | 列出 MCP 资源 |

### A2A API
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/.well-known/agent-card/:agentId` | Agent Card 发现 |
| POST | `/platform/a2a/tasks` | 创建 A2A 任务 |
| GET | `/platform/a2a/tasks/:id` | 查询任务状态 |

### IAM API
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/iam/auth/login` | 登录 |
| POST | `/iam/auth/refresh` | 刷新 Token |
| POST | `/iam/secrets` | 创建密钥 |
| GET | `/iam/quotas` | 查看配额 |

### 公共端点（无需认证）
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/healthz` | 健康检查 |
| GET | `/metrics` | Prometheus 指标 |
| GET | `/profile` | 服务配置信息 |
| GET | `/api/public/bots/:botId` | 公开 Bot 配置 |

## WebSocket 事件

通过 `/ws` 建立 WebSocket 连接后可接收以下事件：

| 事件 | 说明 |
|------|------|
| `session.message.received` | 收到新消息 |
| `agent.thinking` | Agent 思考中 |
| `agent.output` | Agent 输出 token |
| `agent.completed` | Agent 完成 |
| `dag.progress` | DAG 执行进度 |
| `emergence` | 涌现通信检测 |
| `tool.call` | 工具调用 |
| `error` | 错误事件 |

## 错误码

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 201 | 创建成功 |
| 400 | 请求参数错误 |
| 401 | 未认证 |
| 403 | 无权限 |
| 404 | 资源不存在 |
| 409 | 冲突（如容器未运行） |
| 429 | 限流 |
| 500 | 服务端错误 |
| 502 | 上游服务不可用 |

## 下一步

- [认证说明](/zh/api/authentication)
- [Agent API 详细](/zh/api/agent)
- [WebSocket 事件详细](/zh/api/websocket)
