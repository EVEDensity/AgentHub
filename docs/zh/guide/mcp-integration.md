# MCP 协议集成

AgentHub 完整实现了 Anthropic 的 **Model Context Protocol (MCP)**，支持 STDIO 和 SSE 两种传输方式。

## MCP 是什么？

MCP 是 LLM 与外部工具/数据源之间的标准化通信协议。通过 MCP，Agent 可以：
- 调用外部 API 工具
- 访问知识库和文档
- 执行代码和脚本
- 与其他 MCP 兼容服务互操作

## 架构

```
AgentHub Agent
    │
    ▼
Go MCP Gateway
├ STDIO Transport → 本地 CLI 工具 (Claude Code, Codex CLI)
├ SSE Transport → 远程 MCP 服务
└ Registry → 工具/知识库/记忆注册表
```

## 添加 MCP 工具

### 1. 通过管理后台

管理后台 → **MCP** → **工具注册**：
1. 选择传输方式（STDIO / SSE）
2. 填写工具配置（命令、参数、schema）
3. 注册到 Agent

### 2. 通过 API

```bash
POST /api/mcp/tools
{
  "name": "web_search",
  "description": "Search the web using a search engine",
  "transport": "stdio",
  "config": {
    "command": "python",
    "args": ["-m", "web_search_tool"]
  },
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": { "type": "string", "description": "Search query" }
    },
    "required": ["query"]
  }
}
```

## MCP Resources

AgentHub 将以下资源暴露为 MCP Resources：

| Resource | URI Pattern | 说明 |
|----------|------------|------|
| 知识库文档 | `knowledge://{collection}/{doc_id}` | 阅读知识库文档 |
| Agent 记忆 | `memory://agent/{agent_id}` | 访问 Agent 记忆 |
| 工作流 | `workflow://{workflow_id}` | 执行/查询工作流 |
| Agent 状态 | `agent://{agent_id}/status` | 查询 Agent 运行状态 |

## 接入本地 CLI Agent

AgentHub 可以自动发现并注册本机的 CLI Agent：

```bash
# 自动扫描注册
agenthub-cli agent discover

# 手动注册
agenthub-cli agent register \
  --name "claude-code" \
  --command "claude" \
  --args "--resume" \
  --transport "stdio"
```

支持的 CLI Agent：Claude Code、Codex CLI、OpenClaw、以及任何 MCP 兼容的本地工具。

## 下一步

- [MCP 协议深入](/zh/advanced/mcp-deep-dive)
- [MCP Gateway API 参考](/zh/api/mcp-gateway)
