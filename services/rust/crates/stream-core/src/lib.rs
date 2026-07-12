//! # stream-core
//!
//! AgentHub 平台的高性能流式核心，提供三大能力：
//!
//! 1. **chunk 合并**（[`merger::ChunkMerger`]）：把上游高频 token delta 按 session
//!    聚合成较大批次下发，降低帧数 10× 量级。
//! 2. **背压窗口**（[`backpressure::BackpressureChannel`]）：有界 channel + 满时
//!    降级策略（Block/DropOldest/DropNewest/Coalesce），防止上游压垮下游。
//! 3. **慢消费者降级**（[`consumer::ConsumerRegistry`]）：每消费者独立 channel +
//!    lag 阈值监控 + 超时回收，避免一个慢连接拖垮整个实例。
//!
//! 三者由 [`core::StreamCore`] 组合成完整管线，通过 NATS 接入 stream-delivery。
//! 详见各模块文档。

pub mod backpressure;
pub mod chunk;
pub mod consumer;
pub mod core;
pub mod merger;
pub mod nats;
pub mod server;

// ── 顶层 re-export（常用类型直接可 `use stream_core::X`）──────────────
pub use backpressure::{
    BackpressureChannel, BackpressureConfig, BackpressureStats, Coalesceable, FullAction,
};
pub use chunk::{ChunkKind, ChunkMeta, FlushReason, FlushedBatch, StreamChunk};
pub use consumer::{
    ConsumerConfig, ConsumerHandle, ConsumerId, ConsumerRegistry, ConsumerStats, LagReport,
    SlowConsumerPolicy,
};
pub use core::{StreamCore, StreamCoreConfig, StreamCoreStats};
pub use merger::{default_flush_policy, ChunkMerger, FlushPolicy};

/// Crate 版本（与 Cargo workspace 一致）。
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
