//! StreamCore：顶层编排器，把 [`ChunkMerger`] + [`BackpressureChannel`] +
//! [`ConsumerRegistry`] 组合成一条完整的处理管线：
//!
//! ```text
//!   NATS stream.events
//!        │  (StreamChunk)
//!        ▼
//!   ┌──────────────────┐
//!   │ BackpressureChannel│  ← 满时按 FullAction 降级
//!   └────────┬─────────┘
//!            ▼
//!   ┌──────────────────┐
//!   │   ChunkMerger    │  ← 按 session 聚合，FlushPolicy 触发
//!   └────────┬─────────┘
//!            ▼  (FlushedBatch)
//!   ┌──────────────────┐
//!   │ ConsumerRegistry │  ← fanout 到各消费者，慢消费者降级
//!   └──────────────────┘
//!            ▼
//!   WebSocket / SSE 客户端
//! ```
//!
//! StreamCore 持有以上三个组件，并运行两个后台 task：
//! - ingest loop：从 backpressure rx 取 chunk → 喂给 merger → flush 出的 batch 发给 registry。
//! - interval flush ticker：周期性调 `merger.poll_due` 处理 interval 阈值。
//!
//! 此外暴露 [`StreamCore::ingest`] 供 NATS 订阅回调同步推入，以及
//! [`StreamCore::subscribe`] 供 HTTP handler 创建消费者。

use std::sync::Arc;
use std::time::Duration;
use tokio::sync::Mutex;
use tokio::task::JoinHandle;

use crate::backpressure::{BackpressureChannel, BackpressureConfig, Coalesceable};
use crate::chunk::{ChunkKind, FlushedBatch, StreamChunk};
use crate::consumer::{ConsumerConfig, ConsumerHandle, ConsumerRegistry, ConsumerStats, LagReport};
use crate::merger::{ChunkMerger, FlushPolicy};

impl Coalesceable for StreamChunk {
    fn coalesce(&mut self, other: &Self) {
        // 仅 delta 可合并；其余类型直接拼接 content。
        if self.kind != ChunkKind::Delta {
            // 非 delta：保留 self，丢弃 other 内容（不合并）。
            return;
        }
        self.content.push_str(&other.content);
        self.meta.sequence = other.meta.sequence.max(self.meta.sequence);
    }
}

/// StreamCore 顶层配置。
#[derive(Debug, Clone)]
pub struct StreamCoreConfig {
    pub flush_policy: FlushPolicy,
    pub backpressure: BackpressureConfig,
    pub consumer: ConsumerConfig,
    /// interval flush ticker 周期。
    pub flush_tick: Duration,
    /// idle consumer 回收周期。
    pub reap_tick: Duration,
}

impl Default for StreamCoreConfig {
    fn default() -> Self {
        Self {
            flush_policy: FlushPolicy::default(),
            backpressure: BackpressureConfig::default(),
            consumer: ConsumerConfig::default(),
            flush_tick: Duration::from_millis(30),
            reap_tick: Duration::from_secs(15),
        }
    }
}

/// 全局运行时统计（聚合 merger + backpressure + registry 的关键指标）。
#[derive(Debug, Default, Clone, serde::Serialize)]
pub struct StreamCoreStats {
    pub ingested: u64,
    pub batches_emitted: u64,
    pub backpressure: crate::backpressure::BackpressureStats,
    pub merger_buffered_chunks: usize,
    pub merger_active_sessions: usize,
    pub active_sessions: usize,
    pub active_consumers: usize,
    pub degraded_total: u64,
}

/// StreamCore：持有所有组件并驱动后台循环。
pub struct StreamCore {
    config: StreamCoreConfig,
    merger: Arc<Mutex<ChunkMerger>>,
    bp: Arc<BackpressureChannel<StreamChunk>>,
    registry: Arc<ConsumerRegistry>,
    /// 每个 session 的序号分配器（单调递增）。
    seq: Arc<Mutex<std::collections::HashMap<String, u64>>>,
    stats: Arc<std::sync::Mutex<StreamCoreStats>>,
}

impl StreamCore {
    pub fn new(config: StreamCoreConfig) -> Arc<Self> {
        let merger = Arc::new(Mutex::new(ChunkMerger::new(config.flush_policy)));
        let bp = Arc::new(BackpressureChannel::new(config.backpressure.clone()));
        let registry = Arc::new(ConsumerRegistry::new(config.consumer.clone()));
        Arc::new(Self {
            config,
            merger,
            bp,
            registry,
            seq: Arc::new(Mutex::new(std::collections::HashMap::new())),
            stats: Arc::new(std::sync::Mutex::new(StreamCoreStats::default())),
        })
    }

    pub fn config(&self) -> &StreamCoreConfig {
        &self.config
    }

    pub fn backpressure(&self) -> &Arc<BackpressureChannel<StreamChunk>> {
        &self.bp
    }

    pub fn registry(&self) -> &Arc<ConsumerRegistry> {
        &self.registry
    }

    /// 分配该 session 的下一个 chunk 序号（单调递增）。
    async fn next_seq(&self, session_id: &str) -> u64 {
        let mut seqs = self.seq.lock().await;
        let next = seqs.entry(session_id.to_string()).or_insert(0);
        *next += 1;
        *next
    }

    /// 入口：上游（NATS 订阅）调此方法推入一个 chunk。
    /// 会先分配序号，再经背压窗口入队。背压满时按策略降级。
    pub async fn ingest(&self, mut chunk: StreamChunk) -> Result<(), StreamChunk> {
        // 分配 session 内单调序号。
        chunk.meta.sequence = self.next_seq(&chunk.meta.session_id).await;
        self.bump(|s| s.ingested += 1);
        self.bp.send(chunk).await
    }

    /// 订阅一个 session 的输出流（消费者侧）。
    pub async fn subscribe(&self, session_id: &str, consumer_id: Option<&str>) -> ConsumerHandle {
        self.registry.subscribe(session_id, consumer_id).await
    }

    pub async fn unsubscribe(&self, session_id: &str, consumer_id: &str) {
        self.registry.unsubscribe(session_id, consumer_id).await;
    }

    /// 启动后台驱动循环。返回所有 task 的 join handle，便于优雅停机。
    pub fn spawn(self: Arc<Self>) -> Vec<JoinHandle<()>> {
        let mut handles = Vec::new();

        // 1. ingest loop：从背压 rx 取 chunk → merger → fanout。
        let core = Arc::clone(&self);
        handles.push(tokio::spawn(async move {
            core.run_ingest_loop().await;
        }));

        // 2. interval flush ticker：周期性 flush 到期的缓冲区。
        let core = Arc::clone(&self);
        let tick = self.config.flush_tick;
        handles.push(tokio::spawn(async move {
            let mut ticker = tokio::time::interval(tick);
            ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
            loop {
                ticker.tick().await;
                core.interval_flush().await;
            }
        }));

        // 3. idle consumer reaper。
        let core = Arc::clone(&self);
        let reap = self.config.reap_tick;
        handles.push(tokio::spawn(async move {
            let mut ticker = tokio::time::interval(reap);
            ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
            loop {
                ticker.tick().await;
                let removed = core.registry.reap_idle().await;
                if removed > 0 {
                    tracing::info!(removed, "reaped idle consumers");
                }
            }
        }));

        handles
    }

    /// 关闭前一次性 flush 所有缓冲区并 fanout。
    pub async fn shutdown_flush(&self) {
        let batches = {
            let mut m = self.merger.lock().await;
            m.flush_all()
        };
        for b in batches {
            self.fanout_batch(b).await;
        }
    }

    async fn run_ingest_loop(&self) {
        let rx = self.bp.receiver();
        loop {
            // 阻塞等待下一个 chunk。
            let chunk = {
                let mut r = rx.lock().await;
                r.recv().await
            };
            let chunk = match chunk {
                Some(c) => c,
                None => {
                    // channel 关闭（所有 sender drop），退出循环。
                    tracing::info!("backpressure channel closed, ingest loop exiting");
                    break;
                }
            };
            let batches = {
                let mut m = self.merger.lock().await;
                m.push(chunk)
            };
            for b in batches {
                self.fanout_batch(b).await;
            }
        }
    }

    async fn interval_flush(&self) {
        let batches = {
            let mut m = self.merger.lock().await;
            m.poll_due()
        };
        for b in batches {
            self.fanout_batch(b).await;
        }
    }

    async fn fanout_batch(&self, batch: FlushedBatch) {
        self.bump(|s| s.batches_emitted += 1);
        let reports = self.registry.fanout(batch).await;
        if !reports.is_empty() {
            self.bump(|s| s.degraded_total += reports.len() as u64);
            for r in reports.iter().take(3) {
                tracing::warn!(
                    consumer_id = %r.consumer_id,
                    session_id = %r.session_id,
                    lag = r.lag,
                    policy = ?r.policy_applied,
                    "slow consumer degraded"
                );
            }
        }
    }

    /// 全量统计快照。
    pub async fn stats(&self) -> StreamCoreStats {
        let mut s = self.stats.lock().unwrap().clone();
        s.backpressure = self.bp.stats();
        {
            let m = self.merger.lock().await;
            s.merger_buffered_chunks = m.buffered_chunks();
            s.merger_active_sessions = m.active_sessions();
        }
        s.active_sessions = self.registry.active_sessions().await;
        s.active_consumers = self.registry.active_consumers().await;
        let cs = self.registry.stats().await;
        s.degraded_total = s.degraded_total.max(cs.iter().map(|c| c.degraded).sum());
        s
    }

    pub async fn consumer_stats(&self) -> Vec<ConsumerStats> {
        self.registry.stats().await
    }

    pub async fn lag_reports(&self) -> Vec<LagReport> {
        self.registry.recent_lag_reports().await
    }

    fn bump<F: FnOnce(&mut StreamCoreStats)>(&self, f: F) {
        let mut s = self.stats.lock().unwrap();
        f(&mut s);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::chunk::ChunkMeta;
    use chrono::Utc;

    fn delta(session: &str, content: &str) -> StreamChunk {
        StreamChunk {
            kind: ChunkKind::Delta,
            meta: ChunkMeta {
                tenant_id: "t".into(),
                session_id: session.into(),
                message_id: "m".into(),
                trace_id: "tr".into(),
                sequence: 0, // 由 StreamCore 分配
                produced_at: Utc::now(),
            },
            content: content.into(),
            extra: Default::default(),
        }
    }

    fn complete(session: &str) -> StreamChunk {
        StreamChunk {
            kind: ChunkKind::Complete,
            meta: ChunkMeta {
                tenant_id: "t".into(),
                session_id: session.into(),
                message_id: "m".into(),
                trace_id: "tr".into(),
                sequence: 0,
                produced_at: Utc::now(),
            },
            content: "[DONE]".into(),
            extra: Default::default(),
        }
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn end_to_end_merge_and_deliver() {
        let mut cfg = StreamCoreConfig::default();
        // 小阈值，确保测试中能 flush。
        cfg.flush_policy.max_buffered_chunks = 3;
        cfg.flush_policy.max_flush_interval = Duration::from_millis(10);
        cfg.flush_tick = Duration::from_millis(5);
        let core = StreamCore::new(cfg);

        // 启动后台驱动循环（ingest / interval flush / reaper）。
        let handles = Arc::clone(&core).spawn();

        // 订阅。
        let h = core.subscribe("s1", Some("c1")).await;

        // 推 3 个 delta → 触发 count flush。
        core.ingest(delta("s1", "Hel")).await.unwrap();
        core.ingest(delta("s1", "lo ")).await.unwrap();
        core.ingest(delta("s1", "World")).await.unwrap();

        // 等待 ingest loop 处理（很短）。
        tokio::time::sleep(Duration::from_millis(80)).await;

        // 应收到一个合并批次 "Hello World"。
        let b = h.try_next().await;
        assert!(b.is_some(), "should receive a merged batch");
        assert_eq!(b.unwrap().merged_content, "Hello World");

        for hd in handles {
            hd.abort();
        }
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn terminal_chunk_produces_terminal_batch() {
        let mut cfg = StreamCoreConfig::default();
        cfg.flush_policy.max_buffered_chunks = 100; // 不触发 count
        cfg.flush_policy.max_flush_interval = Duration::from_secs(60);
        cfg.flush_tick = Duration::from_millis(200);
        let core = StreamCore::new(cfg);
        let handles = Arc::clone(&core).spawn();
        let h = core.subscribe("s1", Some("c1")).await;
        core.ingest(delta("s1", "partial")).await.unwrap();
        core.ingest(complete("s1")).await.unwrap();
        tokio::time::sleep(Duration::from_millis(80)).await;

        // 应收到 2 个：partial flush + terminal。
        let b1 = h.try_next().await;
        let b2 = h.try_next().await;
        assert!(b1.is_some(), "should receive partial flush");
        assert!(b2.is_some(), "should receive terminal batch");
        assert!(!b1.unwrap().terminal);
        assert!(b2.unwrap().terminal);

        for hd in handles {
            hd.abort();
        }
    }

    #[tokio::test]
    async fn sequence_assigned_monotonically() {
        let cfg = StreamCoreConfig::default();
        let core = StreamCore::new(cfg);
        // 直接测 next_seq。
        assert_eq!(core.next_seq("s1").await, 1);
        assert_eq!(core.next_seq("s1").await, 2);
        assert_eq!(core.next_seq("s2").await, 1);
    }
}
