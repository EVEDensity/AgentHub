# 架构总览

AgentHub 采用 **Go + Rust + Python** 三层微服务架构，通过 NATS JetStream 事件总线实现异步解耦。

## 架构图

```
                         ┌──────────────────────────┐
                         │    Frontend (Next.js 13)   │
                         │  🎨 Warm Studio 2.0       │
                         │  ├ Typed 组件库            │
                         │  ├ WebGL 粒子拓扑          │
                         │  ├ RAG 文档查看器           │
                         │  └ 自适应响应式布局         │
                         └────────────┬─────────────┘
                                      │
         ┌────────────────────────────┼────────────────────────────┐
         │                            │                            │
         ▼                            ▼                            ▼
┌──────────────┐   ┌──────────────────────┐   ┌──────────────────┐
│  接入层 (Go)  │   │   编排层 (Go)         │   │  生态层 (Go)     │
│              │   │                      │   │                  │
│ Gateway      │   │ Realtime-Orchestrator│   │ Channel-Connector│
│ Session      │   │  ├ ReactMachine      │   │  ├ Feishu        │
│ Stream-Delivery│  │  ├ DeepSearch       │   │  ├ WeCom         │
│ MCP-Gateway  │   │  ├ RustCoreBridge    │   │  └ API-Public    │
│ A2A-Handler  │   │  ├ AgentNet-Orch     │   │ Sandbox-Service │
└──────┬───────┘   └─────────┬────────────┘   └────────┬─────────┘
       │                     │                          │
       └─────────────────────┼──────────────────────────┘
                             │
           ┌─────────────────┴─────────────────┐
           │       NATS JetStream (12 条)        │
           └─────────────────┬─────────────────┘
                             │
  ┌──────────────┬───────────┼───────────┬──────────────┐
  │              │           │           │              │
  ▼              ▼           ▼           ▼              ▼
┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────────┐
│ Context │ │ stream- │ │retrieval│ │ fanout- │ │ agentnet-│
│ Engine  │ │ core    │ │-core    │ │ core    │ │ core     │
│  (Go)   │ │ (Rust)  │ │ (Rust)  │ │ (Rust)  │ │ (Rust)   │
└─────────┘ └─────────┘ └─────────┘ └─────────┘ └──────────┘

              ┌────────────────────────────────┐
              │       离线层 (Python ×5)         │
              │  model-adapter | knowledge      │
              │  document-pipeline | summarization│
              │  evaluation | embedding_client  │
              └────────────────────────────────┘

              ┌────────────────────────────────┐
              │       数据层                     │
              │  PostgreSQL | Redis | Qdrant    │
              │  OpenSearch | MinIO | NATS      │
              └────────────────────────────────┘
```

## 分层说明

### 接入层 (Go)

| 服务 | 端口 | 职责 |
|------|------|------|
| **gateway-service** | 8081 | API 入口，WebSocket 网关，JWT 鉴权，限流 |
| **session-service** | 8082 | 会话管理，Redis 热缓存 + PG 持久化 |
| **stream-delivery-service** | 8083 | SSE 流式推送，Redis Streams 持久化回放 |
| **mcp-gateway** | 8099 | MCP 协议网关，STDIO + SSE 双传输 |

### 编排层 (Go)

| 服务 | 职责 |
|------|------|
| **realtime-orchestrator** | ReAct 状态机 (11 状态)，DeepSearch 7 步检索，AgentNet 任务调度 |
| **tool-permission-service** | 敏感工具分级 + 二次确认 |
| **agent-runtime-control-plane** | Worker pool 限流，调用 model-adapter |

### 高性能层 (Rust)

| Crate | 职责 | P95 延迟 |
|-------|------|---------|
| **stream-core** | chunk 合并 + 背压窗口 + 慢消费者降级 | < 10ms |
| **retrieval-core** | BM25+dense 混合检索 + RRF 融合 + rerank | < 80ms |
| **fanout-core** | 高基数 fanout + 通道分区 + 广播调度 | < 5ms |
| **patch-merge-core** | LCS diff + diff3 三路合并 + 冲突打分 | < 20ms |
| **memory-segment-core** | 消息段压缩 + 窗口裁剪 + summary checkpoint | < 15ms |
| **agentnet-core** | DAG 操作 + 任务调度 + 涌现通信 | < 1ms |

### 离线层 (Python)

| 服务 | 职责 |
|------|------|
| **model-adapter-service** | 多模型适配 (OpenAI/Anthropic/BGE/Ollama) |
| **offline-knowledge-service** | 文档分块 + embedding + Qdrant 入库 |
| **document-pipeline-service** | PDF/DOCX/PPTX 抽取 |
| **summarization-service** | 会话总结 + 周期性 consolidation |
| **evaluation-batch-service** | 质量打分 + 回归评测 |

## 事件驱动

所有服务通过 **NATS JetStream** 异步通信。12 条持久化 Stream 确保：

- 消息不丢失（durable consumer + 72h 保留）
- 服务间解耦（发布-订阅模式）
- 可回放（JetStream replay）

## 下一步

- [ContextOS 4 层记忆](/zh/advanced/contextos)
- [K8s 生产部署](/zh/advanced/k8s-deployment)
- [性能调优](/zh/advanced/performance)
