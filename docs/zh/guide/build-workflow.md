# 构建工作流

工作流 (Workflow) 是 AgentHub 中将多个 Agent 编排为 DAG（有向无环图）的核心机制。

## 工作流节点类型

AgentHub v5.1 支持 9 种节点类型：

| 节点类型 | 说明 | 配置要点 |
|---------|------|---------|
| **Start** | 工作流入口，接收触发输入 | 定义输入变量 |
| **Agent** | 调用一个 Agent 执行任务 | 选择 Agent + 提示词模板 |
| **Tool** | 调用 MCP 工具或内置工具 | 选择工具 + 参数映射 |
| **Code** | 执行 Python/JavaScript 代码 | 语言选择 + 超时设置 |
| **HTTP** | 调用外部 REST API | URL + method + headers |
| **Knowledge** | 检索知识库文档 | 集合名 + 查询模板 + Top-K |
| **Human** | 人工审批/确认节点 | 审批人 + 超时 |
| **If/Else** | 条件分支 | 变量比较规则 |
| **End** | 工作流终点，输出结果 | 输出变量映射 |

## 变量引擎

工作流使用 `{{node_id.field}}` 语法引用上下游节点的输出：

```
{{codegen.output}}      → CodeGen 节点的输出
{{http.status}}         → HTTP 节点的状态码
{{knowledge.results}}   → 知识库检索结果
```

### 支持的变量操作

- **点号路径**: `{{node.result.code}}`
- **数组索引**: `{{node.items[0].name}}`
- **嵌套引用**: `{{orchestrator.output.summary.title}}`

## 创建示例工作流

### 1. 新建工作流

管理后台 → **工作流** → **创建** → 填写名称和触发关键词

### 2. 添加节点

从节点面板拖拽需要的节点到画布：

```
[Start] → [Agent: 任务拆解] → [Agent: 代码生成] → [Code: 后处理] → [End]
```

### 3. 配置依赖

拖拽连线建立节点间的依赖关系。每个节点的输入自动来自其上游节点的输出。

### 4. 设置条件分支

在 If/Else 节点中添加条件规则：

```
条件: {{codegen.output.score}} > 85
True → Agent: 代码发布
False → Agent: 代码修正
```

## 触发方式

- **关键词触发**：用户在聊天中发送包含触发关键词的消息
- **API 触发**：`POST /api/workflows/:id/execute`
- **定时触发**：通过 AgentNet Cron 调度

## 下一步

- [AgentNet 多 Agent 协作](/zh/guide/agentnet) — 高级工作流编排
- [工作流 API 参考](/zh/api/workflow) — 程序化管理工作流
