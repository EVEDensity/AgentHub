//! memory-segment-core 数据模型。
//!
//! 一个 [`Message`] 是会话中的一条消息（role + content + token_count + sequence）。
//! 压缩把多条旧消息合并成一个 [`SummarySegment`]；裁剪直接丢弃旧消息；
//! checkpoint 把整段消息压缩成可持久化的 [`Checkpoint`]。
//!
//! 数据流：
//! ```text
//!   Vec<Message> ──┬── compact_messages ──→ CompactResult { summary, retained }
//!                  │
//!                  ├── prune_messages ────→ PruneResult { pruned, retained }
//!                  │
//!                  └── build_checkpoint ──→ Checkpoint { summary_text, ... }
//! ```

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// 消息角色。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MessageRole {
    System,
    User,
    Assistant,
    Tool,
}

impl MessageRole {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::System => "system",
            Self::User => "user",
            Self::Assistant => "assistant",
            Self::Tool => "tool",
        }
    }

    pub fn from_str(s: &str) -> Self {
        match s.to_ascii_lowercase().as_str() {
            "system" => Self::System,
            "tool" => Self::Tool,
            "assistant" => Self::Assistant,
            _ => Self::User,
        }
    }
}

/// 单条会话消息。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Message {
    /// 单调递增的会话内序号。
    pub sequence: u64,
    pub role: MessageRole,
    pub content: String,
    /// token 数（由调用方提供，或用 [`estimate_tokens`](crate::compact::estimate_tokens) 估算）。
    pub token_count: usize,
    /// unix epoch 秒（可选）。
    #[serde(default)]
    pub timestamp: Option<i64>,
}

impl Message {
    /// 构造一条消息并自动估算 token 数。
    pub fn new(sequence: u64, role: MessageRole, content: impl Into<String>) -> Self {
        let content = content.into();
        let token_count = estimate_tokens(&content, &CompactionConfig::default());
        Self {
            sequence,
            role,
            content,
            token_count,
            timestamp: None,
        }
    }
}

/// 压缩/裁剪配置。
///
/// 实现 `Deserialize` 并带 `#[serde(default)]`，允许 HTTP/NATS 调用方只传
/// 部分字段（缺失字段用 [`Default`]）。与 nats.rs 的 `CompactionConfigHelper`
/// 行为等价（默认值相同），保留 helper 仅为兼容既有测试。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct CompactionConfig {
    /// 触发压缩的消息数阈值（默认 40，与原桩 `compactable_window` 一致）。
    pub compact_trigger_messages: usize,
    /// token 预算上限（超此值也触发压缩，默认 32000）。
    pub max_tokens: usize,
    /// 压缩时保留的最近消息数（默认 10）。
    pub keep_recent: usize,
    /// token 估算的每 token 字符数（默认 4.0，即 4 字符 ≈ 1 token）。
    pub chars_per_token: f64,
    /// 摘要是否包含 header（默认 true）。
    pub summary_header: bool,
}

impl Default for CompactionConfig {
    fn default() -> Self {
        Self {
            compact_trigger_messages: 40,
            max_tokens: 32_000,
            keep_recent: 10,
            chars_per_token: 4.0,
            summary_header: true,
        }
    }
}

/// 压缩生成的摘要段：替代被压缩的多条消息。
#[derive(Debug, Clone, Serialize)]
pub struct SummarySegment {
    /// 摘要文本（v1 为结构化拼接，真实摘要由 summarization-service 异步生成）。
    pub content: String,
    pub token_count: usize,
    /// 覆盖的原始消息序号区间 [start, end]。
    pub covered_sequence_start: u64,
    pub covered_sequence_end: u64,
    pub covered_message_count: usize,
}

/// 压缩结果。
#[derive(Debug, Clone, Serialize)]
pub struct CompactResult {
    /// 是否实际执行了压缩（未达阈值时为 false）。
    pub compacted: bool,
    /// 触发原因："message_count" / "token_budget" / None。
    pub trigger_reason: Option<String>,
    /// 压缩生成的摘要段（compacted=false 时为 None）。
    pub summary: Option<SummarySegment>,
    /// 保留的最近消息（compacted=false 时为全部原消息）。
    pub retained: Vec<Message>,
    /// 被压缩的消息数。
    pub compacted_count: usize,
    pub tokens_before: usize,
    pub tokens_after: usize,
    pub token_reduction: usize,
}

/// 裁剪结果。
#[derive(Debug, Clone, Serialize)]
pub struct PruneResult {
    /// 被丢弃的旧消息。
    pub pruned: Vec<Message>,
    /// 保留的最近消息。
    pub retained: Vec<Message>,
    pub pruned_count: usize,
    pub tokens_before: usize,
    pub tokens_after: usize,
    pub token_reduction: usize,
}

/// Summary checkpoint：可持久化的会话压缩快照。
#[derive(Debug, Clone, Serialize)]
pub struct Checkpoint {
    pub summary_text: String,
    pub token_count: usize,
    pub covered_sequence_start: u64,
    pub covered_sequence_end: u64,
    pub covered_message_count: usize,
    pub created_at: DateTime<Utc>,
}

/// 运行时统计。
#[derive(Debug, Default, Clone, Serialize)]
pub struct MemorySegmentStats {
    pub compacts_total: u64,
    pub compacts_triggered: u64,
    pub prunes_total: u64,
    pub checkpoints_total: u64,
    pub messages_compacted_total: u64,
    pub tokens_reduced_total: u64,
    pub avg_compact_latency_ms: u64,
    pub avg_prune_latency_ms: u64,
    pub avg_checkpoint_latency_ms: u64,
    /// 各操作的最近计数（监控用）。
    pub op_counts: HashMap<String, u64>,
}

/// token 估算：`text` 字符数 / `chars_per_token`，至少 1。
pub fn estimate_tokens(text: &str, config: &CompactionConfig) -> usize {
    if config.chars_per_token <= 0.0 {
        return 1;
    }
    // 注意：`as usize` 后不能直接跟方法调用，需先括号再 .max(1)。
    (((text.chars().count() as f64) / config.chars_per_token).ceil() as usize).max(1)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn message_role_roundtrip() {
        assert_eq!(MessageRole::from_str("user"), MessageRole::User);
        assert_eq!(MessageRole::from_str("ASSISTANT"), MessageRole::Assistant);
        assert_eq!(MessageRole::from_str("unknown"), MessageRole::User);
        assert_eq!(MessageRole::Tool.as_str(), "tool");
    }

    #[test]
    fn message_new_estimates_tokens() {
        let m = Message::new(1, MessageRole::User, "hello world"); // 11 chars / 4 ≈ 3
        assert!(m.token_count >= 1);
        assert_eq!(m.sequence, 1);
    }

    #[test]
    fn estimate_tokens_handles_empty() {
        let c = CompactionConfig::default();
        assert_eq!(estimate_tokens("", &c), 1); // 至少 1
    }

    #[test]
    fn estimate_tokens_respects_ratio() {
        let c = CompactionConfig { chars_per_token: 4.0, ..Default::default() };
        // 8 chars / 4 = 2 tokens
        assert_eq!(estimate_tokens("12345678", &c), 2);
    }

    #[test]
    fn config_defaults_sane() {
        let c = CompactionConfig::default();
        assert_eq!(c.compact_trigger_messages, 40);
        assert!(c.keep_recent >= 1);
        assert!(c.max_tokens > c.keep_recent);
    }

    #[test]
    fn compactable_window_uses_threshold() {
        assert!(!compactable_window_when(39));
        assert!(compactable_window_when(40));
    }

    // 辅助：直接用默认阈值的桩等价检查。
    fn compactable_window_when(n: usize) -> bool {
        n >= CompactionConfig::default().compact_trigger_messages
    }
}
