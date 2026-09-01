# AgentHub 文档

欢迎来到 **AgentHub** — 面向可验证 AI 工作执行的自托管平台。

## 什么是 AgentHub？

AgentHub 当前处于 Mission-centric 重构阶段。长期目标是把一个 Issue 或
API 目标转换为可恢复、可审计、附带 Artifact 和 Evidence 的执行结果。

> 文档声明：以下能力按当前代码分为“已实现、原型、迁移中”。方案文档不
> 等于实现证明；请以代码、迁移和测试为准。

### 核心能力

| 能力 | 当前状态 | 说明 |
|---|---|---|
| Mission / WorkUnit | 已实现，持续补齐 | 当前唯一推荐的新业务状态模型 |
| AgentNet / Legacy DAG | 已实现，迁移中 | 兼容现有功能，不再作为新写模型 |
| MCP Gateway | 原型 | STDIO + SSE 已有，业务无状态化待完成 |
| A2A | 已实现（生产路径） | 出站/入站经 runner 派发（`A2A_DISPATCH_MODE=runner`）：Agent Card、能力探测、双向签名信任、Mission 任务接口与对等结果导出已落地。实现见 [a2a_adapter_service](../app/services/a2a_adapter_service.py)、[a2a_outbound_runner](../app/services/a2a_outbound_runner.py)，测试 [tests/services/test_a2a_\*](../tests/services/) |
| RAG / Memory | L0/L1（精简，ADR-0107） | 会话转录 + 会话摘要；L2 向量/L3/procedural 重层已随 web 聊天下线移除，详见 [memory](../app/services/memory/) 与 [ADR-0107](architecture/decisions/0107-memory-slimming-web-chat-decommission.md) |
| LLM 网关（new-api，可选） | 条件可用 | `AGENTHUB_LLM_GATEWAY=newapi` 启用，通道/迁移/验证脚本见 [deploy/newapi](../deploy/newapi/README.md)，决策 [ADR-0104](architecture/decisions/0104-optional-newapi-llm-gateway.md) |
| 多模态（视觉输入） | 已实现（ADR-0105） | 协议双轨/附件管线/预算计费/工具回传/护栏门禁全链路落地；e2e 探针 `multimodal_e2e_probe` 入 CI（无渠道密钥时诚实 SKIP）。实现见 [adapter_manager](../app/services/adapter_manager.py)、[multimodal 包](../app/services/tools/multimodal/__init__.py)，测试 [test_message_content_dual_track](../tests/services/test_message_content_dual_track.py) |
| 沙箱、IAM、审计 | 部分实现 | 部署能力与安全边界按环境逐项验证 |

### 技术栈

| 层级 | 技术 | 组件 |
|------|------|------|
| 控制面 | Python | Mission、Contract、WorkUnit、事件与 API |
| 执行与协议 | Go | Gateway、Runner 相关服务、MCP/A2A 适配 |
| 性能组件 | Rust | 流处理、检索和合并等可独立扩展核心 |
| 数据与事件 | PostgreSQL、对象存储、NATS | 业务真相、Artifact 和异步事件 |
| 可选上下文 | Qdrant、OpenSearch、Redis | 检索、缓存和会话能力，按部署启用 |
| 前端 | Next.js 13 | 控制面投影和用户审查界面 |

## Documentation map

- [Architecture boundaries](./architecture/)
- [多Agent协作技术方案总图](./architecture/multi-agent-collaboration.md)（事件日志即记忆：会话协作、@Agent 触发、receipts 溯源）
- [North Star（开发者优先的 CLI 体验路线图）](./roadmaps/north-star-developer-cli-experience.md)
- [Delivery roadmap（重构与交付路线图）](./roadmaps/reconstruction-roadmap.md)
- [多Agent记忆架构调研与演进路线](./roadmaps/multi-agent-memory-architecture.md)（Buzz 对标、四大范式、差距与分阶段计划）
- [Documentation governance](./governance/documentation-standard.md)
- [Code quality standard（代码质量与审查机制）](./governance/code-quality-standard.md)
- [Chinese user and API guides](./zh/)
- Detailed private target architecture is kept locally in `docs/internal/` and
  is intentionally excluded from releases.

### 开源

AgentHub 基于 [Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0) 协议开源。
