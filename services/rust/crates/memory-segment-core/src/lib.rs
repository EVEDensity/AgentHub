//! # memory-segment-core
//!
//! AgentHub 平台的上下文窗口压缩引擎，提供消息段压缩、窗口裁剪与
//! summary checkpoint 能力：
//!
//! 1. **段压缩**（[`compact`]）：当消息数或 token 超阈值时，把最旧的
//!    `N - keep_recent` 条消息合并成一个 [`SummarySegment`]（v1 用结构化
//!    拼接做占位摘要，真实 LLM 摘要由 summarization-service 经 NATS 异步生成），
//!    保留最近 `keep_recent` 条原消息，显著降低 token 占用。
//! 2. **窗口裁剪**（[`prune`]）：直接丢弃超出 `keep_recent` 的旧消息（不生成摘要），
//!    用于硬性 token 上限场景。
//! 3. **Summary checkpoint**（[`checkpoint`]）：把整段消息压缩成可持久化的
//!    [`Checkpoint`]，作为会话恢复时的上下文前缀。
//!
//! 设计原则：**纯函数式计算核心**——输入消息列表 + 配置，输出结果，不持有
//! 会话状态。会话窗口的状态管理由 realtime-orchestrator / session-service 负责，
//! 本核心只做性能热点计算（符合"Rust 只做性能热点不做编排"铁律）。
//!
//! 接入方式：通过 NATS 订阅 `agenthub.memory.compact.requested`，处理后发布
//! `memory.compact.completed` 到 `agenthub.memory.audit`。详见 [`nats::NatsAdapter`]。
//!
//! 降级链：
//! - NATS 不可用 → 降级为 HTTP-only 模式（仍可同步 /compact /prune /checkpoint）。
//!
//! 启动顺序：HTTP 服务先 `tokio::spawn`，NATS 连接留主 future——确保 K8s
//! liveness 探针在 NATS 重试期间也能拿到 `/healthz`。

pub mod checkpoint;
pub mod compact;
pub mod core;
pub mod nats;
pub mod prune;
pub mod server;
pub mod types;

// ── 顶层 re-export（常用类型直接可 `use memory_segment_core::X`）────────
pub use checkpoint::build_checkpoint;
pub use compact::compact_messages;
pub use core::{MemorySegmentCore, MemorySegmentCoreHealth};
pub use prune::prune_messages;
pub use types::{
    estimate_tokens, Checkpoint, CompactResult, CompactionConfig, Message, MessageRole,
    PruneResult, SummarySegment,
};

/// Crate 版本（与 Cargo workspace 一致）。
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// 保留原桩函数签名，避免破坏既有调用点（仅作能力探测）。
/// 判定一个消息窗口是否达到压缩阈值（默认 ≥40 条）。
pub fn compactable_window(message_count: usize) -> bool {
    message_count >= CompactionConfig::default().compact_trigger_messages
}
