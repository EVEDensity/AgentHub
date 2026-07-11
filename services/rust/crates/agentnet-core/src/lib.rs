//! # agentnet-core
//!
//! AgentHub 平台的动态 DAG 编排引擎，提供去中心化多智能体协作的核心能力：
//!
//! 1. **DAG 拓扑管理**（[`core::DagEngine`]）：运行时 `add_node` / `remove_node` /
//!    `add_edge` / `reroute`，支持有向无环图的动态修改与拓扑排序验证。
//! 2. **任务分配策略**（[`strategies`]）：四种内置策略 — `round-robin`（轮询）、
//!    `least-loaded`（最少负载）、`capability-match`（能力匹配）、
//!    `cost-optimized`（成本优化）。通过 [`core::AssignmentPolicy`] 枚举切换。
//! 3. **就绪节点计算**（[`core::DagEngine::ready_nodes`]）：基于依赖完成状态的
//!    拓扑就绪判定 — 仅当所有上游依赖已 `completed` 时节点进入 `ready` 状态。
//! 4. **NATS 集成**（[`nats::NatsAgentNetAdapter`]）：订阅 `agenthub.agentnet.*`
//!    subject 族，接收能力宣告、任务发布、spawn 请求、共享记忆等事件，
//!    处理后发布结果到 `agenthub.agentnet.results`。
//!
//! 接入方式：通过 NATS 订阅 `agenthub.agentnet.tasks` 接收任务编排请求，
//! 处理后发布 `agentnet.task.completed` 到 `agenthub.agentnet.results`。
//!
//! 降级链：
//! - NATS 不可用 → 降级为 HTTP-only 模式（仍可同步 `/dag` API），不消费事件。
//! - DAG 拓扑非法（含环） → 返回错误，拒绝修改。
//!
//! 启动顺序：HTTP 服务先 `tokio::spawn`，确保 K8s liveness 探针立即可用。

pub mod core;
pub mod nats;
pub mod server;
pub mod types;

// ── Re-exports ────────────────────────────────────────────────────────
pub use core::{AgentRegistry, AssignmentPolicy, DagEngine, TaskAssigner};
pub use types::{
    AgentCapability, AgentNetConfig, AgentNetStats, AgentSpawn, Dag, DagEdge, DagNode,
    SharedMemoryEntry, Task, TaskResult, TaskStatus,
};

/// Crate version (matches Cargo workspace).
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
