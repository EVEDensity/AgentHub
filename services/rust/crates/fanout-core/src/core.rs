//! FanoutCore：顶层编排器，串联 partition → route → broadcast → receipt。
//!
//! ```text
//!   agenthub.fanout.events (NATS)
//!        │
//!        ▼
//!   ┌─────────────────┐
//!   │ HashPartitioner │  partition_key → partition_id
//!   └────────┬────────┘
//!            │
//!            ▼
//!   ┌─────────────────┐
//!   │   FanoutCore    │  channel → Vec<Arc<SubscriberHandle>>
//!   │   (registry)    │  并行 dispatch，慢订阅者按策略降级
//!   └────────┬────────┘
//!            │
//!            ▼
//!   agenthub.fanout.audit (NATS)
//!        EventEnvelope { event_type: "fanout.event.delivered" }
//! ```
//!
//! 锁顺序约定（避免死锁）：
//! 1. `subscribers`（RwLock）— 持锁时间最短，仅取快照。
//! 2. `stats`（Mutex）— 持锁期间不 acquire 其他锁。
//! 3. `partitioner`（RwLock）— 独立 acquire，不与 1/2 交叠。

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Instant;

use tokio::sync::{mpsc, Mutex, RwLock};

use crate::partitioner::{recommended_partition_count, HashPartitioner};
use crate::types::{
    ChannelStats, DeliveryReceipt, FanoutConfig, FanoutEvent, FanoutStats, SubscriberStats,
};

/// 订阅者句柄：持有 mpsc sender，由 FanoutCore 内部使用。
/// 字段在创建后不可变，故无需 RwLock 包装——`Arc<SubscriberHandle>` 即可。
pub struct SubscriberHandle {
    pub id: String,
    pub channel: String,
    pub tx: mpsc::Sender<FanoutEvent>,
}

impl SubscriberHandle {
    pub fn new(id: String, channel: String, tx: mpsc::Sender<FanoutEvent>) -> Self {
        Self { id, channel, tx }
    }
}

/// FanoutCore：持有分区器、订阅者注册表与运行时统计。
pub struct FanoutCore {
    config: FanoutConfig,
    partitioner: RwLock<HashPartitioner>,
    /// channel → 订阅者列表。
    subscribers: RwLock<HashMap<String, Vec<Arc<SubscriberHandle>>>>,
    stats: Arc<Mutex<FanoutStats>>,
}

impl FanoutCore {
    pub fn new(config: FanoutConfig) -> Arc<Self> {
        let initial = config.initial_partitions;
        Arc::new(Self {
            partitioner: RwLock::new(HashPartitioner::new(initial)),
            subscribers: RwLock::new(HashMap::new()),
            stats: Arc::new(Mutex::new(FanoutStats {
                partition_count: initial,
                ..Default::default()
            })),
            config,
        })
    }

    pub fn config(&self) -> &FanoutConfig {
        &self.config
    }

    /// 订阅一个频道。返回 `(subscriber_id, mpsc::Receiver)` 用于接收事件。
    pub async fn subscribe(
        &self,
        channel: &str,
        subscriber_id: Option<String>,
    ) -> (String, mpsc::Receiver<FanoutEvent>) {
        let id = subscriber_id.unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
        let (tx, rx) = mpsc::channel(self.config.subscriber_capacity);
        let handle = Arc::new(SubscriberHandle::new(id.clone(), channel.to_string(), tx));

        // 持锁顺序：subscribers → stats（与 unsubscribe / stats 一致）。
        let active_subscribers = {
            let mut subs = self.subscribers.write().await;
            subs.entry(channel.to_string()).or_default().push(handle);
            let mut s = self.stats.lock().await;
            s.active_subscribers += 1;
            s.active_channels = subs.len();
            s.active_subscribers
        }; // subs 和 s 在此释放。

        // 扩容检查（独立于 subscribers/stats 锁，避免长时间持锁）。
        if let Some(new_count) = self.maybe_scale_partitions(active_subscribers).await {
            let mut s = self.stats.lock().await;
            s.partition_count = new_count;
        }

        tracing::info!(
            channel = channel,
            subscriber_id = %id,
            active_subscribers,
            "subscriber registered"
        );
        (id, rx)
    }

    /// 退订指定频道的订阅者。返回是否成功移除。
    pub async fn unsubscribe(&self, channel: &str, subscriber_id: &str) -> bool {
        let mut subs = self.subscribers.write().await;
        if let Some(list) = subs.get_mut(channel) {
            let before = list.len();
            list.retain(|h| h.id != subscriber_id);
            let removed = before - list.len();
            if list.is_empty() {
                subs.remove(channel);
            }
            if removed > 0 {
                let mut s = self.stats.lock().await;
                s.active_subscribers = s.active_subscribers.saturating_sub(removed);
                s.active_channels = subs.len();
                tracing::info!(
                    channel = channel,
                    subscriber_id = subscriber_id,
                    "subscriber removed"
                );
                return true;
            }
        }
        false
    }

    /// 路由一个事件：计算分区 → 找到频道订阅者 → 并行 dispatch。
    pub async fn route(&self, mut event: FanoutEvent) -> DeliveryReceipt {
        let start = Instant::now();

        // 1. 计算分区（持 partitioner 读锁，持锁时间极短）。
        let partition = {
            let p = self.partitioner.read().await;
            p.partition(&event.partition_key)
        };
        event.partition = partition;

        // 2. 取该频道的订阅者快照（持 subscribers 读锁，仅 clone Arc 列表）。
        let snapshot: Vec<Arc<SubscriberHandle>> = {
            let subs = self.subscribers.read().await;
            subs.get(&event.channel).cloned().unwrap_or_default()
        };

        let subscriber_count = snapshot.len();
        let mut delivered = 0usize;
        let mut dropped = 0usize;

        // 3. 顺序 dispatch（try_send 非阻塞，单条 < 1μs，无需并行）。
        //    慢订阅者缓冲满 → try_send 返回 Full，按策略降级。
        for handle in &snapshot {
            match handle.tx.try_send(event.clone()) {
                Ok(()) => delivered += 1,
                Err(mpsc::error::TrySendError::Full(_)) => {
                    dropped += 1;
                    tracing::warn!(
                        subscriber_id = %handle.id,
                        channel = %event.channel,
                        policy = self.config.slow_subscriber_policy.as_str(),
                        "subscriber buffer full, dropping event"
                    );
                }
                Err(mpsc::error::TrySendError::Closed(_)) => {
                    dropped += 1;
                    tracing::warn!(
                        subscriber_id = %handle.id,
                        channel = %event.channel,
                        "subscriber channel closed, dropping event"
                    );
                }
            }
        }

        let elapsed_ms = start.elapsed().as_millis() as u64;
        let degraded = dropped > 0;

        let receipt = DeliveryReceipt {
            event_id: event.event_id.clone(),
            channel: event.channel.clone(),
            partition,
            subscriber_count,
            delivered,
            dropped,
            elapsed_ms,
            degraded,
        };

        // 4. 更新统计（内部会检查扩容）。
        self.update_stats(&event, &receipt).await;

        receipt
    }

    /// 当前统计快照。
    pub async fn stats(&self) -> FanoutStats {
        self.stats.lock().await.clone()
    }

    /// 各频道订阅者统计。
    pub async fn subscriber_stats(&self) -> Vec<SubscriberStats> {
        let subs = self.subscribers.read().await;
        let s = self.stats.lock().await;
        let mut out = Vec::with_capacity(subs.len());
        for (channel, list) in subs.iter() {
            out.push(SubscriberStats {
                channel: channel.clone(),
                subscriber_count: list.len(),
                events_total: *s.channel_counts.get(channel).unwrap_or(&0),
                events_dropped: 0, // per-subscriber drop 细分留给后续迭代。
            });
        }
        out
    }

    /// 各频道视角统计。
    pub async fn channel_stats(&self) -> Vec<ChannelStats> {
        let subs = self.subscribers.read().await;
        let s = self.stats.lock().await;
        let mut out = Vec::with_capacity(subs.len());
        for (channel, list) in subs.iter() {
            out.push(ChannelStats {
                channel: channel.clone(),
                subscriber_count: list.len(),
                events_total: *s.channel_counts.get(channel).unwrap_or(&0),
                partition_keys: 0, // 不在 hot path 维护，留空。
            });
        }
        out
    }

    /// 当前分区数。
    pub async fn partition_count(&self) -> usize {
        self.partitioner.read().await.partitions()
    }

    /// 健康状态：fanout-core 无下游依赖，永远 ready（仅 NATS 可用性独立判断）。
    pub fn health(&self) -> FanoutCoreHealth {
        FanoutCoreHealth { ready: true }
    }

    /// 自动扩容分区（订阅者数突破阈值时）。
    /// 返回 `Some(new_count)` 表示已扩容，`None` 表示无需调整。
    async fn maybe_scale_partitions(&self, active_subscribers: usize) -> Option<usize> {
        let target = recommended_partition_count(active_subscribers);
        let mut p = self.partitioner.write().await;
        let current = p.partitions();
        if target > current && target <= self.config.max_partitions {
            p.resize(target);
            tracing::info!(
                before = current,
                after = target,
                active_subscribers,
                "partition count auto-scaled"
            );
            Some(target)
        } else {
            None
        }
    }

    /// 更新运行时统计 + 检查扩容。
    async fn update_stats(&self, event: &FanoutEvent, receipt: &DeliveryReceipt) {
        let active_subscribers = {
            let mut s = self.stats.lock().await;
            s.events_total += 1;
            s.events_delivered += receipt.delivered as u64;
            s.events_dropped += receipt.dropped as u64;
            if receipt.degraded {
                s.degraded_total += 1;
            }
            // 累积平均延迟。
            let n = s.events_total;
            s.avg_latency_ms = ((s.avg_latency_ms as u128 * (n - 1) as u128
                + receipt.elapsed_ms as u128)
                / n as u128) as u64;
            // 频道计数。
            *s.channel_counts.entry(event.channel.clone()).or_insert(0) += 1;
            s.active_subscribers
        }; // s 释放。

        // 扩容检查（独立于 stats 锁）。
        if let Some(new_count) = self.maybe_scale_partitions(active_subscribers).await {
            let mut s = self.stats.lock().await;
            s.partition_count = new_count;
        }
    }
}

/// 健康状态快照。
#[derive(Debug, Clone, serde::Serialize)]
pub struct FanoutCoreHealth {
    pub ready: bool,
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Utc;

    fn make_event(channel: &str, key: &str) -> FanoutEvent {
        FanoutEvent {
            event_id: format!("evt-{}", key),
            event_type: "test.event".into(),
            channel: channel.into(),
            partition_key: key.into(),
            partition: 0,
            tenant_id: "t".into(),
            session_id: "s".into(),
            trace_id: "tr".into(),
            occurred_at: Utc::now(),
            payload: serde_json::json!({"k": key}),
        }
    }

    #[tokio::test]
    async fn route_delivers_to_all_subscribers() {
        let core = FanoutCore::new(FanoutConfig {
            subscriber_capacity: 16,
            ..Default::default()
        });
        let (_id1, mut rx1) = core.subscribe("session", None).await;
        let (_id2, mut rx2) = core.subscribe("session", None).await;

        let receipt = core.route(make_event("session", "key-1")).await;
        assert_eq!(receipt.subscriber_count, 2);
        assert_eq!(receipt.delivered, 2);
        assert_eq!(receipt.dropped, 0);
        assert!(!receipt.degraded);

        assert!(rx1.try_recv().is_ok());
        assert!(rx2.try_recv().is_ok());
    }

    #[tokio::test]
    async fn route_to_empty_channel_returns_zero_delivered() {
        let core = FanoutCore::new(FanoutConfig::default());
        let receipt = core.route(make_event("audit", "k")).await;
        assert_eq!(receipt.subscriber_count, 0);
        assert_eq!(receipt.delivered, 0);
        assert!(!receipt.degraded);
    }

    #[tokio::test]
    async fn unsubscribe_removes_subscriber() {
        let core = FanoutCore::new(FanoutConfig::default());
        let (id1, _rx1) = core.subscribe("session", Some("sub-1".into())).await;
        let (_id2, _rx2) = core.subscribe("session", Some("sub-2".into())).await;

        let removed = core.unsubscribe("session", &id1).await;
        assert!(removed);

        let receipt = core.route(make_event("session", "k")).await;
        assert_eq!(receipt.subscriber_count, 1);
    }

    #[tokio::test]
    async fn unsubscribe_unknown_id_returns_false() {
        let core = FanoutCore::new(FanoutConfig::default());
        let (_id, _rx) = core.subscribe("session", Some("sub-1".into())).await;
        let removed = core.unsubscribe("session", "nonexistent").await;
        assert!(!removed);
    }

    #[tokio::test]
    async fn slow_subscriber_drops_events() {
        // 容量 1，不消费 → 第二条事件触发 drop。
        let core = FanoutCore::new(FanoutConfig {
            subscriber_capacity: 1,
            ..Default::default()
        });
        let (_id, _rx) = core.subscribe("session", None).await;
        // 第一条进入缓冲。
        let r1 = core.route(make_event("session", "k1")).await;
        assert_eq!(r1.delivered, 1);
        // 第二条触发 drop（缓冲满）。
        let r2 = core.route(make_event("session", "k2")).await;
        assert_eq!(r2.delivered, 0);
        assert_eq!(r2.dropped, 1);
        assert!(r2.degraded);
    }

    #[tokio::test]
    async fn partition_count_auto_scales_on_subscribe() {
        let core = FanoutCore::new(FanoutConfig {
            initial_partitions: 8,
            max_partitions: 32,
            partition_scale_threshold: 1000,
            ..Default::default()
        });
        assert_eq!(core.partition_count().await, 8);
        // 注入 1000 个订阅者触发扩容到 16。
        for _ in 0..1000 {
            let _ = core.subscribe("bulk", None).await;
        }
        assert_eq!(core.partition_count().await, 16);
    }

    #[tokio::test]
    async fn partition_count_scales_to_32_at_5000() {
        let core = FanoutCore::new(FanoutConfig {
            initial_partitions: 8,
            max_partitions: 32,
            ..Default::default()
        });
        for _ in 0..5000 {
            let _ = core.subscribe("bulk", None).await;
        }
        assert_eq!(core.partition_count().await, 32);
    }

    #[tokio::test]
    async fn stats_track_events_and_channels() {
        let core = FanoutCore::new(FanoutConfig::default());
        let (_id, _rx) = core.subscribe("session", None).await;
        let (_id2, _rx2) = core.subscribe("audit", None).await;

        core.route(make_event("session", "k1")).await;
        core.route(make_event("session", "k2")).await;
        core.route(make_event("audit", "k3")).await;

        let s = core.stats().await;
        assert_eq!(s.events_total, 3);
        assert_eq!(s.events_delivered, 3);
        assert_eq!(s.active_channels, 2);
        assert_eq!(s.active_subscribers, 2);
        assert_eq!(*s.channel_counts.get("session").unwrap(), 2);
        assert_eq!(*s.channel_counts.get("audit").unwrap(), 1);
    }

    #[tokio::test]
    async fn route_assigns_partition_to_event() {
        let core = FanoutCore::new(FanoutConfig {
            initial_partitions: 16,
            ..Default::default()
        });
        let (_id, mut rx) = core.subscribe("session", None).await;
        core.route(make_event("session", "my-key")).await;
        let evt = rx.recv().await.unwrap();
        assert!(evt.partition < 16);
    }
}
