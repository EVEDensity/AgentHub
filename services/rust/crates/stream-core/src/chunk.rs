//! 流式 chunk 数据模型。
//!
//! 一个 [`StreamChunk`] 表示 LLM 流式输出的一个最小片段（token delta），
//! 或一个显式的流控标记（flush / complete / error / heartbeat）。
//! chunk 是 stream-core 内部流转的统一数据单元，所有合并、背压、降级逻辑
//! 都围绕它展开。

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// chunk 语义类型。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ChunkKind {
    /// 增量 token 片段（最常见）。
    Delta,
    /// 显式 flush 标记：要求立即把缓冲区刷出，不参与合并。
    Flush,
    /// 流终止：这是该 message 的最后一个 chunk。
    Complete,
    /// 错误标记：上游发生错误，附带错误信息。
    Error,
    /// 心跳保活，不承载业务内容。
    Heartbeat,
}

impl ChunkKind {
    /// 是否为可合并的增量片段。只有 [`ChunkKind::Delta`] 才参与合并。
    pub fn is_mergeable(self) -> bool {
        matches!(self, ChunkKind::Delta)
    }

    /// 是否为流终止信号（Complete / Error）。
    pub fn is_terminal(self) -> bool {
        matches!(self, ChunkKind::Complete | ChunkKind::Error)
    }
}

/// chunk 元数据：用于路由、排序、去重、回放。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChunkMeta {
    pub tenant_id: String,
    pub session_id: String,
    /// 该 chunk 所属的 message（一次 LLM 回复）ID。
    pub message_id: String,
    pub trace_id: String,
    /// 该 chunk 在 session 内的单调递增序号（由 stream-core 分配）。
    pub sequence: u64,
    /// 上游产生时间。
    pub produced_at: DateTime<Utc>,
}

/// stream-core 的核心数据单元。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StreamChunk {
    pub kind: ChunkKind,
    pub meta: ChunkMeta,
    /// 文本内容（Delta 时为 token 片段；Error 时为错误描述；其余可能为空）。
    pub content: String,
    /// 透传的额外字段，避免业务侧扩展时改动核心结构。
    #[serde(default)]
    pub extra: BTreeMap<String, serde_json::Value>,
}

impl StreamChunk {
    /// 内容字节数（UTF-8）。
    pub fn byte_len(&self) -> usize {
        self.content.len()
    }

    /// 从 platform-events 的 EventEnvelope 载荷构造一个 Delta chunk。
    /// `sequence` 由调用方（StreamCore）分配。
    pub fn from_delta_envelope(
        env: &platform_events::EventEnvelope,
        content: String,
        sequence: u64,
    ) -> Self {
        Self {
            kind: ChunkKind::Delta,
            meta: ChunkMeta {
                tenant_id: env.tenant_id.clone(),
                session_id: env.session_id.clone(),
                message_id: env.message_id.clone().unwrap_or_default(),
                trace_id: env.trace_id.clone(),
                sequence,
                produced_at: env.occurred_at,
            },
            content,
            extra: env.payload.clone().into_iter().collect(),
        }
    }

    /// 生成一个心跳 chunk。
    pub fn heartbeat(tenant_id: &str, session_id: &str, sequence: u64) -> Self {
        Self {
            kind: ChunkKind::Heartbeat,
            meta: ChunkMeta {
                tenant_id: tenant_id.to_string(),
                session_id: session_id.to_string(),
                message_id: String::new(),
                trace_id: String::new(),
                sequence,
                produced_at: Utc::now(),
            },
            content: String::new(),
            extra: BTreeMap::new(),
        }
    }
}

/// 刷出的合并批次：由 [`crate::merger::ChunkMerger`] 产出，下发到消费者。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FlushedBatch {
    pub session_id: String,
    pub tenant_id: String,
    pub message_id: String,
    pub trace_id: String,
    /// 合并后的文本内容。
    pub merged_content: String,
    /// 该批次包含的 chunk 数。
    pub chunk_count: usize,
    /// 合并后字节数。
    pub byte_size: usize,
    /// 首个 chunk 的序号。
    pub first_sequence: u64,
    /// 末个 chunk 的序号。
    pub last_sequence: u64,
    /// 触发本次 flush 的原因。
    pub reason: FlushReason,
    /// 是否为终止批次（包含 Complete/Error）。
    pub terminal: bool,
    /// 产生时间。
    pub emitted_at: DateTime<Utc>,
}

/// flush 触发原因。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FlushReason {
    /// 缓冲 chunk 数达到上限。
    Count,
    /// 合并字节数达到上限。
    Size,
    /// 定时器到点。
    Interval,
    /// 收到显式 Flush / Complete / Error chunk。
    Explicit,
    /// 关闭前最后一次 flush。
    Shutdown,
}

impl FlushReason {
    pub fn as_str(self) -> &'static str {
        match self {
            FlushReason::Count => "count",
            FlushReason::Size => "size",
            FlushReason::Interval => "interval",
            FlushReason::Explicit => "explicit",
            FlushReason::Shutdown => "shutdown",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn delta(seq: u64, content: &str) -> StreamChunk {
        StreamChunk {
            kind: ChunkKind::Delta,
            meta: ChunkMeta {
                tenant_id: "t".into(),
                session_id: "s".into(),
                message_id: "m".into(),
                trace_id: "tr".into(),
                sequence: seq,
                produced_at: Utc::now(),
            },
            content: content.to_string(),
            extra: BTreeMap::new(),
        }
    }

    #[test]
    fn byte_len_counts_utf8_bytes() {
        let c = delta(1, "你好");
        assert_eq!(c.byte_len(), 6); // 2 个汉字 = 6 字节
    }

    #[test]
    fn kind_classification() {
        assert!(ChunkKind::Delta.is_mergeable());
        assert!(!ChunkKind::Flush.is_mergeable());
        assert!(ChunkKind::Complete.is_terminal());
        assert!(ChunkKind::Error.is_terminal());
        assert!(!ChunkKind::Heartbeat.is_terminal());
    }
}
