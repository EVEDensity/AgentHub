//! # fanout-core
//!
//! AgentHub 平台的高基数事件 fanout 路由器，提供 channel 分区、广播调度
//! 与慢订阅者降级能力：
//!
//! 1. **分区路由**（[`partitioner::HashPartitioner`]）：FNV-1a 哈希把
//!    `partition_key` 映射到 `[0, partitions)` 区间；分区数随活跃订阅者数
//!    自动扩容（8 → 16 → 32，由 [`partitioner::recommended_partition_count`]
//!    决定）。
//! 2. **频道广播**（[`core::FanoutCore`]）：每个频道维护订阅者列表，
//!    事件到来时并行 `try_send` 到所有订阅者；缓冲满则按
//!    [`SlowSubscriberPolicy`] 降级（drop_oldest / drop_newest / coalesce）。
//! 3. **事件接入**（[`nats::NatsAdapter`]）：订阅
//!    `agenthub.fanout.events`，把带 `routing.channel` 的
//!    [`EventEnvelope`](platform_events::EventEnvelope) 投影为
//!    [`FanoutEvent`]，路由后发布 `fanout.event.delivered` 到
//!    `agenthub.fanout.audit`。
//!
//! 接入方式：通过 NATS 订阅 `agenthub.fanout.events`，处理后发布
//! `fanout.event.delivered` 到 `agenthub.fanout.audit`。详见
//! [`nats::NatsAdapter`]。
//!
//! 降级链：
//! - 订阅者缓冲满 → 按 `slow_subscriber_policy` 丢弃事件，仍向其他订阅者投递。
//! - NATS 不可用 → 降级为 HTTP-only 模式（仍可同步 `/route`），不消费事件。
//!
//! 启动顺序：HTTP 服务先 `tokio::spawn`，NATS 连接留主 future——确保 K8s
//! liveness 探针在 NATS 重试期间也能拿到 `/healthz`。

pub mod core;
pub mod nats;
pub mod partitioner;
pub mod server;
pub mod types;

// ── 顶层 re-export（常用类型直接可 `use fanout_core::X`）────────────
pub use core::{FanoutCore, FanoutCoreHealth, SubscriberHandle};
pub use partitioner::{recommended_partition_count, HashPartitioner};
pub use types::{
    ChannelStats, DeliveryReceipt, FanoutConfig, FanoutEvent, FanoutStats, SlowSubscriberPolicy,
    SubscriberStats,
};

/// Crate 版本（与 Cargo workspace 一致）。
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
