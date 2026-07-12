//! fanout-core 数据模型。
//!
//! 一个 [`FanoutEvent`] 来自 NATS subject `agenthub.fanout.events`，
//! 携带 `routing.channel`（如 "session"/"audit"/"broadcast"）与
//! `routing.partition_key`。fanout-core 根据 partition_key 哈希到分区，
//! 然后广播给所有订阅了该 channel 的活跃订阅者。
//!
//! 数据流：
//! ```text
//!   agenthub.fanout.events (NATS)
//!        │  EventEnvelope { routing.channel, routing.partition_key, payload }
//!        ▼
//!   ┌──────────────────┐
//!   │  HashPartitioner │  partition_key → partition_id
//!   └────────┬─────────┘
//!            │
//!            ▼
//!   ┌──────────────────┐
//!   │   FanoutCore     │  channel → Vec<SubscriberHandle>
//!   │   (registry)     │  并行 dispatch，慢订阅者按策略降级
//!   └────────┬─────────┘
//!            │
//!            ▼
//!   agenthub.fanout.audit (NATS)
//!        EventEnvelope { event_type: "fanout.event.delivered", payload: DeliveryReceipt }
//! ```

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::time::Duration;

/// 慢订阅者降级策略。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SlowSubscriberPolicy {
    /// 丢弃最旧的事件（默认，保最新）。
    DropOldest,
    /// 丢弃最新的事件（保历史完整）。
    DropNewest,
    /// 合并相邻同类事件（仅保留首条 + 计数；当前实现等价于 DropOldest）。
    Coalesce,
}

impl SlowSubscriberPolicy {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::DropOldest => "drop_oldest",
            Self::DropNewest => "drop_newest",
            Self::Coalesce => "coalesce",
        }
    }
}

impl Default for SlowSubscriberPolicy {
    fn default() -> Self {
        Self::DropOldest
    }
}

/// fanout-core 配置。
#[derive(Debug, Clone)]
pub struct FanoutConfig {
    /// 初始分区数（启动时使用）。
    pub initial_partitions: usize,
    /// 分区上限（auto-scale 不超过此值）。
    pub max_partitions: usize,
    /// 触发分区扩容的活跃订阅者阈值（信息性，实际阈值由
    /// [`recommended_partition_count`](crate::partitioner::recommended_partition_count) 决定）。
    pub partition_scale_threshold: usize,
    /// 每订阅者缓冲容量（mpsc channel 容量）。
    pub subscriber_capacity: usize,
    /// 慢订阅者降级策略。
    pub slow_subscriber_policy: SlowSubscriberPolicy,
    /// 统计快照刷新周期（信息性，实际由 binary 决定日志频率）。
    pub stats_tick: Duration,
}

impl Default for FanoutConfig {
    fn default() -> Self {
        Self {
            initial_partitions: 8,
            max_partitions: 32,
            partition_scale_threshold: 1000,
            subscriber_capacity: 256,
            slow_subscriber_policy: SlowSubscriberPolicy::default(),
            stats_tick: Duration::from_secs(30),
        }
    }
}

/// fanout 事件：从 EventEnvelope 投影出的路由视图。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FanoutEvent {
    pub event_id: String,
    pub event_type: String,
    /// 频道名（如 "session"/"audit"/"broadcast"）。来自 envelope.routing.channel。
    pub channel: String,
    /// 分区键（通常为 session_id / tenant_id）。来自 envelope.routing.partition_key；
    /// 缺失时回退到 envelope.session_id。
    pub partition_key: String,
    /// 计算出的分区 ID（0..partitions）。由 [`FanoutCore::route`](crate::core::FanoutCore::route) 填充。
    pub partition: usize,
    pub tenant_id: String,
    pub session_id: String,
    pub trace_id: String,
    pub occurred_at: DateTime<Utc>,
    /// 原始 payload（透传给订阅者）。
    pub payload: serde_json::Value,
}

/// 单次广播的投递回执：发布为 `fanout.event.delivered`。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeliveryReceipt {
    pub event_id: String,
    pub channel: String,
    pub partition: usize,
    pub subscriber_count: usize,
    pub delivered: usize,
    pub dropped: usize,
    pub elapsed_ms: u64,
    pub degraded: bool,
}

/// 运行时统计。
#[derive(Debug, Default, Clone, Serialize)]
pub struct FanoutStats {
    pub events_total: u64,
    pub events_delivered: u64,
    pub events_dropped: u64,
    pub degraded_total: u64,
    pub active_subscribers: usize,
    pub active_channels: usize,
    pub partition_count: usize,
    pub avg_latency_ms: u64,
    /// 各频道的事件计数（用于 top-N 监控）。
    pub channel_counts: HashMap<String, u64>,
}

/// 订阅者视角的统计。
#[derive(Debug, Default, Clone, Serialize)]
pub struct SubscriberStats {
    pub channel: String,
    pub subscriber_count: usize,
    pub events_total: u64,
    pub events_dropped: u64,
}

/// 频道视角的统计。
#[derive(Debug, Default, Clone, Serialize)]
pub struct ChannelStats {
    pub channel: String,
    pub subscriber_count: usize,
    pub events_total: u64,
    pub partition_keys: usize,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slow_subscriber_policy_serializes() {
        let p = SlowSubscriberPolicy::DropOldest;
        let s = serde_json::to_string(&p).unwrap();
        assert_eq!(s, "\"drop_oldest\"");
    }

    #[test]
    fn config_defaults_are_sane() {
        let c = FanoutConfig::default();
        assert!(c.initial_partitions >= 1);
        assert!(c.max_partitions >= c.initial_partitions);
        assert!(c.subscriber_capacity >= 16);
    }
}
