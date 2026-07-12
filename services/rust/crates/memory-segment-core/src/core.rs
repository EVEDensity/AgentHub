//! MemorySegmentCore：顶层编排器，串联 compact / prune / checkpoint 并维护统计。
//!
//! ```text
//!   HTTP /compact ──→ compact::compact_messages ──→ stats(compact)
//!   HTTP /prune ───→ prune::prune_messages ──────→ stats(prune)
//!   HTTP /checkpoint → checkpoint::build_checkpoint → stats(checkpoint)
//!       │
//!       ▼
//!   NATS agenthub.memory.compact.requested
//!       │  EventEnvelope { payload: { messages, config? } }
//!       ▼
//!   compact_messages → CompactResult
//!       │
//!       ▼
//!   NATS agenthub.memory.audit (memory.compact.completed)
//! ```

use std::sync::Arc;
use std::time::Instant;

use tokio::sync::Mutex;

use crate::checkpoint::build_checkpoint;
use crate::compact::compact_messages;
use crate::prune::prune_messages;
use crate::types::{
    Checkpoint, CompactResult, CompactionConfig, MemorySegmentStats, Message, PruneResult,
};

/// memory-segment-core 顶层编排器。
pub struct MemorySegmentCore {
    config: CompactionConfig,
    stats: Arc<Mutex<MemorySegmentStats>>,
}

impl MemorySegmentCore {
    pub fn new(config: CompactionConfig) -> Arc<Self> {
        Arc::new(Self {
            config,
            stats: Arc::new(Mutex::new(MemorySegmentStats::default())),
        })
    }

    pub fn config(&self) -> &CompactionConfig {
        &self.config
    }

    /// 压缩消息列表。
    pub async fn compact(&self, messages: &[Message]) -> CompactResult {
        let start = Instant::now();
        let result = compact_messages(messages, &self.config);
        let elapsed = start.elapsed().as_millis() as u64;
        self.record_compact(elapsed, result.compacted, result.compacted_count, result.token_reduction)
            .await;
        result
    }

    /// 用指定配置压缩（覆盖默认配置，供调用方按会话调整）。
    pub async fn compact_with(
        &self,
        messages: &[Message],
        config: &CompactionConfig,
    ) -> CompactResult {
        let start = Instant::now();
        let result = compact_messages(messages, config);
        let elapsed = start.elapsed().as_millis() as u64;
        self.record_compact(elapsed, result.compacted, result.compacted_count, result.token_reduction)
            .await;
        result
    }

    /// 裁剪消息列表。
    pub async fn prune(&self, messages: &[Message]) -> PruneResult {
        let start = Instant::now();
        let result = prune_messages(messages, &self.config);
        let elapsed = start.elapsed().as_millis() as u64;
        self.record_prune(elapsed, result.token_reduction).await;
        result
    }

    /// 构建 checkpoint。
    pub async fn checkpoint(&self, messages: &[Message]) -> Checkpoint {
        let start = Instant::now();
        let result = build_checkpoint(messages, &self.config);
        let elapsed = start.elapsed().as_millis() as u64;
        self.record_checkpoint(elapsed).await;
        result
    }

    /// 当前统计快照。
    pub async fn stats(&self) -> MemorySegmentStats {
        self.stats.lock().await.clone()
    }

    /// 健康状态：无下游依赖，永远 ready。
    pub fn health(&self) -> MemorySegmentCoreHealth {
        MemorySegmentCoreHealth { ready: true }
    }

    async fn record_compact(
        &self,
        elapsed_ms: u64,
        compacted: bool,
        compacted_count: usize,
        token_reduction: usize,
    ) {
        let mut s = self.stats.lock().await;
        s.compacts_total += 1;
        if compacted {
            s.compacts_triggered += 1;
            s.messages_compacted_total += compacted_count as u64;
            s.tokens_reduced_total += token_reduction as u64;
        }
        s.avg_compact_latency_ms =
            rolling_avg(s.avg_compact_latency_ms, s.compacts_total, elapsed_ms);
        *s.op_counts.entry("compact".into()).or_insert(0) += 1;
    }

    async fn record_prune(&self, elapsed_ms: u64, token_reduction: usize) {
        let mut s = self.stats.lock().await;
        s.prunes_total += 1;
        s.tokens_reduced_total += token_reduction as u64;
        s.avg_prune_latency_ms = rolling_avg(s.avg_prune_latency_ms, s.prunes_total, elapsed_ms);
        *s.op_counts.entry("prune".into()).or_insert(0) += 1;
    }

    async fn record_checkpoint(&self, elapsed_ms: u64) {
        let mut s = self.stats.lock().await;
        s.checkpoints_total += 1;
        s.avg_checkpoint_latency_ms =
            rolling_avg(s.avg_checkpoint_latency_ms, s.checkpoints_total, elapsed_ms);
        *s.op_counts.entry("checkpoint".into()).or_insert(0) += 1;
    }
}

/// 累积平均：`avg = (avg * (n-1) + x) / n`。
fn rolling_avg(prev: u64, n: u64, x: u64) -> u64 {
    if n == 0 {
        return 0;
    }
    ((prev as u128 * (n - 1) as u128 + x as u128) / n as u128) as u64
}

/// 健康状态快照。
#[derive(Debug, Clone, serde::Serialize)]
pub struct MemorySegmentCoreHealth {
    pub ready: bool,
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::MessageRole;

    fn core() -> Arc<MemorySegmentCore> {
        MemorySegmentCore::new(CompactionConfig {
            compact_trigger_messages: 5,
            keep_recent: 2,
            ..Default::default()
        })
    }

    fn msg(seq: u64) -> Message {
        Message::new(seq, MessageRole::User, "hello world")
    }

    #[tokio::test]
    async fn compact_records_stats() {
        let c = core();
        let msgs: Vec<Message> = (1..=10).map(msg).collect();
        let r = c.compact(&msgs).await;
        assert!(r.compacted);
        let s = c.stats().await;
        assert_eq!(s.compacts_total, 1);
        assert_eq!(s.compacts_triggered, 1);
        assert!(s.messages_compacted_total > 0);
    }

    #[tokio::test]
    async fn compact_below_threshold_not_triggered() {
        let c = core();
        let msgs = vec![msg(1), msg(2)];
        let r = c.compact(&msgs).await;
        assert!(!r.compacted);
        let s = c.stats().await;
        assert_eq!(s.compacts_triggered, 0);
        assert_eq!(s.compacts_total, 1); // 仍计数调用
    }

    #[tokio::test]
    async fn prune_records_stats() {
        let c = core();
        let msgs: Vec<Message> = (1..=10).map(msg).collect();
        c.prune(&msgs).await;
        let s = c.stats().await;
        assert_eq!(s.prunes_total, 1);
    }

    #[tokio::test]
    async fn checkpoint_records_stats() {
        let c = core();
        c.checkpoint(&[msg(1)]).await;
        let s = c.stats().await;
        assert_eq!(s.checkpoints_total, 1);
    }

    #[tokio::test]
    async fn health_always_ready() {
        let c = core();
        assert!(c.health().ready);
    }

    #[tokio::test]
    async fn rolling_avg_behaves() {
        assert_eq!(rolling_avg(0, 1, 10), 10);
        assert_eq!(rolling_avg(10, 2, 20), 15);
    }
}
