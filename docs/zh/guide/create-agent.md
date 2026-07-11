# 创建第一个 Agent

本指南将引导你创建第一个 Agent 并与之对话。

## Agent 是什么？

Agent 是 AgentHub 中的智能体单元。每个 Agent 拥有独立的：

- **系统提示词 (System Prompt)**：定义 Agent 的角色和行为
- **模型配置**：选择底层 LLM（支持 50+ 模型供应商）
- **能力标签**：`code_generation`、`diff_review`、`doc_writing` 等
- **工具集**：可调用的 MCP 工具和内置工具

## 创建方式

### 方式一：管理后台（GUI）

1. 登录管理后台 → 点击 **Agent 身份** → **创建 Agent**
2. 填写 Agent 信息：
   - **名称**：如 `CodeReviewBot`
   - **模型**：选择 `claude-sonnet-4-6` 或其他
   - **系统提示词**：定义 Agent 的角色
   - **能力标签**：勾选相关能力
3. 点击保存 → Agent 即刻就绪

### 方式二：API

```bash
curl -X POST http://localhost:8080/api/agents \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "name": "CodeReviewBot",
    "model": "claude-sonnet-4-6",
    "systemPrompt": "You are a senior code reviewer. Focus on security, performance, and code readability.",
    "capabilities": ["code_review", "diff_analysis"],
    "tools": ["code_review", "security_scan"]
  }'
```

### 方式三：模板市场

管理后台 → **模板市场** → 选择任意模板 → 一键创建。

AgentHub 内置了 15 个 Agent 模板，覆盖代码审查、文档写作、测试执行等常见场景。

## 测试你的 Agent

创建完成后，你可以在聊天界面通过 `@Agent名称` 来调用它，例如：

```
@CodeReviewBot 请审查 src/auth/login.ts 中的安全漏洞
```

Agent 将根据其系统提示词和能力标签自主决定如何响应。

## 下一步

- [构建工作流](/zh/guide/build-workflow) — 将多个 Agent 编排为 DAG
- [核心概念](/zh/guide/concepts) — 理解 AgentHub 的架构设计
