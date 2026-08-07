# 当前架构总览

> 文档状态：implemented baseline
> 最后审查：2026-08-08
> 详细目标方案：本地 `docs/internal/architecture/target-architecture.md`

AgentHub 当前是一个正在收敛的多运行时系统。不要把现有服务数量理解成
已经完成的多 Agent 产品；新的业务开发必须围绕 Mission 和 WorkUnit 展开。

## 当前可验证边界

```text
Frontend / API
      |
      v
Mission Control (Python)
  Mission / Contract / WorkUnit / Event Ledger
      |
      +--> Runner + Harness --> Model Adapter / Tools
      |                         |
      |                         +--> Artifact / Evidence
      |
      +--> Legacy adapters: LangGraph / AgentNet
      +--> Protocol adapters: MCP / A2A
```

### Mission Control

负责 Mission、Contract、WorkUnit 的持久化、状态转换、lease、事件和权限。
这是唯一允许新增业务状态的控制面。Mission 的完成状态需要 Artifact 和
Evidence 支持，不能由模型文本或前端状态直接决定。

### Runner 与 Harness

目标是将隔离执行、模型循环、function calling、工具调用、预算、取消、
checkpoint 和证据采集统一起来。当前仍在从旧 Agent/DAG 链路迁移，部署
环境必须明确执行能力和降级行为。

### Legacy 兼容层

LangGraph 和 AgentNet 仍服务部分现有聊天、DAG 和监控功能。它们不是新的
领域真相，也不应继续增加新的任务表、状态枚举或成功语义。

### 协议适配层

- A2A 用于外部 Agent 委托和发现，不负责内部调度。
- MCP 用于工具和资源暴露，不保存 Mission 业务状态。
- 协议请求必须映射到 Mission/WorkUnit，并经过认证、能力和审计检查。

## 部署原则

Community 应支持单控制面、一个 Runner、本地数据库和本地 Artifact Store。
Cloud/Enterprise 才按负载引入队列、对象存储、Runner 池、AI Gateway、
多租户和高可用组件。没有独立扩缩容、安全或运行时边界的数据，不拆为新
微服务。

## 相关文档

- [文档与架构边界](../../architecture/README.md)
- [中文 API 总览](../api/overview.md)
- [AgentNet 兼容说明](../advanced/agentnet-dag.md)
- [MCP 集成说明](../guide/mcp-integration.md)
