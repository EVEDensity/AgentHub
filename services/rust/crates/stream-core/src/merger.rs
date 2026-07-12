//! chunk 合并器：按 session 聚合 [`StreamChunk`]，按 [`FlushPolicy`] 触发 flush。
//!
//! 合并的目标是把上游高频、细粒度的 token delta（典型 10–50ms 一个，几字节）
//! 聚合成较大的批次下发，从而：
//!   - 降低 NATS / WebSocket 帧数（10× 量级）；
//!   - 给慢消费者留出处理预算；
//!   - 让背压窗口以"批次"而非"单 chunk"为单位计量。
//!
//! 合并只针对 [`ChunkKind::Delta`]；Flush/Complete/Error/Heartbeat 立即触发
//! 或终止当前缓冲。每个 session 拥有独立的 [`MergeBuffer`]，互不影响。

use std::collections::HashMap;
use std::time::{Duration, Instant};

use crate::chunk::{ChunkKind, FlushReason, FlushedBatch, StreamChunk};

/// flush 策略：决定何时把缓冲区里的 delta 合并成一个批次刷出。
///
/// 三个阈值任一满足即触发 flush（OR 语义）。`max_flush_interval` 由
/// [`ChunkMerger::poll_due`] 配合定时器驱动。
#[derive(Debug, Clone, Copy)]
pub struct FlushPolicy {
    /// 缓冲区最大 chunk 数。达到即 flush。
    pub max_buffered_chunks: usize,
    /// 两次 flush 之间的最大间隔。到点即 flush（即使 chunk 数不足）。
    pub max_flush_interval: Duration,
    /// 合并后最大字节数。达到即 flush。
    pub max_merged_bytes: usize,
}

impl Default for FlushPolicy {
    fn default() -> Self {
        Self {
            max_buffered_chunks: 12,
            max_flush_interval: Duration::from_millis(120),
            max_merged_bytes: 8 * 1024,
        }
    }
}

/// 向后兼容：原 lib.rs 暴露的 `default_flush_policy`。
pub fn default_flush_policy() -> FlushPolicy {
    FlushPolicy::default()
}

/// 单个 session 的合并缓冲区。
struct MergeBuffer {
    /// 待合并的 delta chunk（仅 Delta 类型）。
    chunks: Vec<StreamChunk>,
    /// 累计字节数（content 拼接后的 UTF-8 长度）。
    total_bytes: usize,
    /// 上次 flush 时刻，用于 interval 判定。
    last_flush: Instant,
    /// 当前缓冲区归属的 message_id（chunk 切换 message 时强制 flush）。
    current_message_id: String,
}

impl MergeBuffer {
    fn new() -> Self {
        Self {
            chunks: Vec::new(),
            total_bytes: 0,
            last_flush: Instant::now(),
            current_message_id: String::new(),
        }
    }

    #[allow(dead_code)]
    fn is_empty(&self) -> bool {
        self.chunks.is_empty()
    }

    /// 推入一个 delta chunk。返回是否因阈值触发 flush（调用方据此调 `flush`）。
    /// 注意：message 切换由 [`ChunkMerger::push`] 在调用本方法前处理，此处只负责
    /// 阈值判定。
    fn push(&mut self, chunk: StreamChunk, policy: &FlushPolicy) -> FlushTrigger {
        self.current_message_id = chunk.meta.message_id.clone();
        self.total_bytes += chunk.byte_len();
        self.chunks.push(chunk);

        if self.chunks.len() >= policy.max_buffered_chunks {
            return FlushTrigger::Count;
        }
        if self.total_bytes >= policy.max_merged_bytes {
            return FlushTrigger::Size;
        }
        FlushTrigger::None
    }

    /// 把缓冲区合并成一个 [`FlushedBatch`] 并清空。
    /// `reason` 由调用方根据触发条件指定。
    fn flush(&mut self, reason: FlushReason) -> Option<FlushedBatch> {
        if self.chunks.is_empty() {
            return None;
        }
        let first = self.chunks.first().unwrap();
        let last = self.chunks.last().unwrap();
        let tenant_id = first.meta.tenant_id.clone();
        let session_id = first.meta.session_id.clone();
        let message_id = first.meta.message_id.clone();
        let trace_id = first.meta.trace_id.clone();
        let first_sequence = first.meta.sequence;
        let last_sequence = last.meta.sequence;

        let mut merged = String::with_capacity(self.total_bytes);
        for c in &self.chunks {
            merged.push_str(&c.content);
        }
        let byte_size = merged.len();
        let chunk_count = self.chunks.len();

        self.chunks.clear();
        self.total_bytes = 0;
        self.last_flush = Instant::now();

        Some(FlushedBatch {
            session_id,
            tenant_id,
            message_id,
            trace_id,
            merged_content: merged,
            chunk_count,
            byte_size,
            first_sequence,
            last_sequence,
            reason,
            terminal: false,
            emitted_at: chrono::Utc::now(),
        })
    }

    /// interval 定时器判定：距上次 flush 是否超过阈值。
    fn is_due(&self, policy: &FlushPolicy) -> bool {
        !self.chunks.is_empty() && self.last_flush.elapsed() >= policy.max_flush_interval
    }
}

/// 内部触发条件。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum FlushTrigger {
    None,
    Count,
    Size,
}

/// chunk 合并器：管理所有 session 的缓冲区。
///
/// 不是异步类型——它只做同步的"推入 + 判定 + flush"。异步驱动由
/// [`crate::core::StreamCore`] 通过定时器 + channel 完成。这样合并器本身
/// 可被单元测试，且无锁竞争（单线程所有权）。
pub struct ChunkMerger {
    policy: FlushPolicy,
    buffers: HashMap<String, MergeBuffer>,
}

impl ChunkMerger {
    pub fn new(policy: FlushPolicy) -> Self {
        Self {
            policy,
            buffers: HashMap::new(),
        }
    }

    pub fn policy(&self) -> &FlushPolicy {
        &self.policy
    }

    /// 当前持有的 session 数（用于指标）。
    pub fn active_sessions(&self) -> usize {
        self.buffers.len()
    }

    /// 当前缓冲的 chunk 总数（用于指标）。
    pub fn buffered_chunks(&self) -> usize {
        self.buffers.values().map(|b| b.chunks.len()).sum()
    }

    /// 推入一个 chunk，返回因该 chunk 产生的 flush 批次（0 或 1 个，部分情况 2 个）。
    ///
    /// 语义：
    /// - Delta：进缓冲区；若触发阈值则 flush。
    /// - Flush：立即 flush 当前缓冲区（reason=Explicit），该 chunk 本身不下发。
    /// - Complete / Error：先 flush 当前缓冲区，再产出一个 terminal 批次
    ///   （内容为该 chunk 的 content，terminal=true）。
    /// - Heartbeat：忽略（不影响缓冲区）。
    pub fn push(&mut self, chunk: StreamChunk) -> Vec<FlushedBatch> {
        let mut out = Vec::with_capacity(2);
        let session_id = chunk.meta.session_id.clone();
        let kind = chunk.kind;

        match kind {
            ChunkKind::Delta => {
                let buf = self.buffers.entry(session_id).or_insert_with(MergeBuffer::new);
                // message 切换：先 flush 旧 message 的缓冲，再推入新 chunk。
                let switch = !buf.current_message_id.is_empty()
                    && buf.current_message_id != chunk.meta.message_id;
                if switch {
                    if let Some(b) = buf.flush(FlushReason::Explicit) {
                        out.push(b);
                    }
                }
                let trigger = buf.push(chunk, &self.policy);
                match trigger {
                    FlushTrigger::None => {}
                    FlushTrigger::Count => {
                        if let Some(b) = buf.flush(FlushReason::Count) {
                            out.push(b);
                        }
                    }
                    FlushTrigger::Size => {
                        if let Some(b) = buf.flush(FlushReason::Size) {
                            out.push(b);
                        }
                    }
                }
            }
            ChunkKind::Flush => {
                if let Some(buf) = self.buffers.get_mut(&session_id) {
                    if let Some(b) = buf.flush(FlushReason::Explicit) {
                        out.push(b);
                    }
                }
            }
            ChunkKind::Complete | ChunkKind::Error => {
                // 先 flush 已缓冲的 delta。
                if let Some(buf) = self.buffers.get_mut(&session_id) {
                    if let Some(b) = buf.flush(FlushReason::Explicit) {
                        out.push(b);
                    }
                }
                // 再产出 terminal 批次。
                let terminal = self.make_terminal(&chunk);
                out.push(terminal);
                // terminal 后该 session 的缓冲区可清理（message 结束）。
                self.buffers.remove(&session_id);
            }
            ChunkKind::Heartbeat => {
                // 心跳不影响合并缓冲区；可选地触发 interval flush。
            }
        }

        out
    }

    fn make_terminal(&self, chunk: &StreamChunk) -> FlushedBatch {
        FlushedBatch {
            session_id: chunk.meta.session_id.clone(),
            tenant_id: chunk.meta.tenant_id.clone(),
            message_id: chunk.meta.message_id.clone(),
            trace_id: chunk.meta.trace_id.clone(),
            merged_content: chunk.content.clone(),
            chunk_count: 1,
            byte_size: chunk.content.len(),
            first_sequence: chunk.meta.sequence,
            last_sequence: chunk.meta.sequence,
            reason: FlushReason::Explicit,
            terminal: true,
            emitted_at: chrono::Utc::now(),
        }
    }

    /// 定时器驱动：把所有"到期"的缓冲区 flush 出来（interval 阈值）。
    /// 应由 [`crate::core::StreamCore`] 周期性调用。
    pub fn poll_due(&mut self) -> Vec<FlushedBatch> {
        let mut out = Vec::new();
        for buf in self.buffers.values_mut() {
            if buf.is_due(&self.policy) {
                if let Some(b) = buf.flush(FlushReason::Interval) {
                    out.push(b);
                }
            }
        }
        out
    }

    /// 关闭前一次性 flush 所有缓冲区。
    pub fn flush_all(&mut self) -> Vec<FlushedBatch> {
        let mut out = Vec::new();
        for buf in self.buffers.values_mut() {
            if let Some(b) = buf.flush(FlushReason::Shutdown) {
                out.push(b);
            }
        }
        self.buffers.clear();
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::chunk::ChunkMeta;
    use chrono::Utc;

    fn delta(seq: u64, content: &str, msg: &str) -> StreamChunk {
        StreamChunk {
            kind: ChunkKind::Delta,
            meta: ChunkMeta {
                tenant_id: "t".into(),
                session_id: "s".into(),
                message_id: msg.into(),
                trace_id: "tr".into(),
                sequence: seq,
                produced_at: Utc::now(),
            },
            content: content.to_string(),
            extra: Default::default(),
        }
    }

    fn complete(seq: u64, msg: &str) -> StreamChunk {
        StreamChunk {
            kind: ChunkKind::Complete,
            meta: ChunkMeta {
                tenant_id: "t".into(),
                session_id: "s".into(),
                message_id: msg.into(),
                trace_id: "tr".into(),
                sequence: seq,
                produced_at: Utc::now(),
            },
            content: "[DONE]".into(),
            extra: Default::default(),
        }
    }

    #[test]
    fn count_trigger_flushes() {
        let mut m = ChunkMerger::new(FlushPolicy {
            max_buffered_chunks: 3,
            max_flush_interval: Duration::from_secs(60),
            max_merged_bytes: 1024,
        });
        // 推 3 个 delta，第 3 个触发 count flush。
        let b1 = m.push(delta(1, "a", "m1"));
        assert!(b1.is_empty());
        let b2 = m.push(delta(2, "b", "m1"));
        assert!(b2.is_empty());
        let b3 = m.push(delta(3, "c", "m1"));
        assert_eq!(b3.len(), 1);
        assert_eq!(b3[0].merged_content, "abc");
        assert_eq!(b3[0].chunk_count, 3);
        assert_eq!(b3[0].reason, FlushReason::Count);
    }

    #[test]
    fn size_trigger_flushes() {
        let mut m = ChunkMerger::new(FlushPolicy {
            max_buffered_chunks: 100,
            max_flush_interval: Duration::from_secs(60),
            max_merged_bytes: 5,
        });
        // "hello" = 5 字节，达到阈值。
        let r = m.push(delta(1, "hello", "m1"));
        assert_eq!(r.len(), 1);
        assert_eq!(r[0].reason, FlushReason::Size);
        assert_eq!(r[0].byte_size, 5);
    }

    #[test]
    fn complete_produces_terminal_batch() {
        let mut m = ChunkMerger::new(FlushPolicy::default());
        m.push(delta(1, "partial", "m1")); // 进缓冲区，不 flush
        let r = m.push(complete(2, "m1"));
        // 应产出 2 个：缓冲区 flush + terminal。
        assert_eq!(r.len(), 2);
        assert_eq!(r[0].merged_content, "partial");
        assert!(!r[0].terminal);
        assert!(r[1].terminal);
        assert_eq!(r[1].merged_content, "[DONE]");
    }

    #[test]
    fn message_switch_flushes_old() {
        let mut m = ChunkMerger::new(FlushPolicy {
            max_buffered_chunks: 100,
            max_flush_interval: Duration::from_secs(60),
            max_merged_bytes: 1024,
        });
        m.push(delta(1, "a", "m1"));
        m.push(delta(2, "b", "m1"));
        // 切换 message：应 flush m1 的 "ab"，新 chunk 留在缓冲区。
        let r = m.push(delta(3, "c", "m2"));
        assert_eq!(r.len(), 1);
        assert_eq!(r[0].merged_content, "ab");
        assert_eq!(r[0].reason, FlushReason::Explicit);
        assert_eq!(m.buffered_chunks(), 1); // "c" 仍在
    }

    #[test]
    fn poll_due_flushes_by_interval() {
        let mut m = ChunkMerger::new(FlushPolicy {
            max_buffered_chunks: 100,
            max_flush_interval: Duration::from_millis(1),
            max_merged_bytes: 1024,
        });
        m.push(delta(1, "a", "m1"));
        std::thread::sleep(Duration::from_millis(5));
        let r = m.poll_due();
        assert_eq!(r.len(), 1);
        assert_eq!(r[0].reason, FlushReason::Interval);
    }
}
