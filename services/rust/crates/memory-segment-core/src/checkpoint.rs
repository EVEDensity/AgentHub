//! Summary checkpoint：把整段消息压缩成可持久化的快照。
//!
//! checkpoint 把**全部**输入消息合并成一个 [`Checkpoint`]（不像 compact 只压旧消息），
//! 用于会话暂停/恢复场景——把当前完整上下文压缩成一个可存储的摘要前缀。
//!
//! v1 摘要为结构化拼接（复用 [`build_summary_segment`](crate::compact::build_summary_segment)）；
//! 真实 LLM 摘要由 summarization-service 异步生成后替换 `summary_text`。

use chrono::Utc;

use crate::compact::build_summary_segment;
use crate::types::{Checkpoint, CompactionConfig, Message};

/// 把全部消息压缩成一个 checkpoint。
pub fn build_checkpoint(messages: &[Message], config: &CompactionConfig) -> Checkpoint {
    let summary = build_summary_segment(messages, config);
    Checkpoint {
        summary_text: summary.content,
        token_count: summary.token_count,
        covered_sequence_start: summary.covered_sequence_start,
        covered_sequence_end: summary.covered_sequence_end,
        covered_message_count: summary.covered_message_count,
        created_at: Utc::now(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::MessageRole;

    fn msg(seq: u64, role: MessageRole, content: &str) -> Message {
        Message::new(seq, role, content)
    }

    #[test]
    fn checkpoint_covers_all_messages() {
        let msgs = vec![
            msg(1, MessageRole::System, "you are helpful"),
            msg(2, MessageRole::User, "hello"),
            msg(3, MessageRole::Assistant, "hi there"),
        ];
        let cp = build_checkpoint(&msgs, &CompactionConfig::default());
        assert_eq!(cp.covered_message_count, 3);
        assert_eq!(cp.covered_sequence_start, 1);
        assert_eq!(cp.covered_sequence_end, 3);
        assert!(cp.summary_text.contains("system"));
        assert!(cp.summary_text.contains("hello"));
        assert!(cp.summary_text.contains("hi there"));
        assert!(cp.token_count >= 1);
    }

    #[test]
    fn checkpoint_empty_messages() {
        let cp = build_checkpoint(&[], &CompactionConfig::default());
        assert_eq!(cp.covered_message_count, 0);
        assert_eq!(cp.covered_sequence_start, 0);
        assert_eq!(cp.covered_sequence_end, 0);
    }

    #[test]
    fn checkpoint_has_timestamp() {
        let cp = build_checkpoint(&[msg(1, MessageRole::User, "x")], &CompactionConfig::default());
        // created_at 应是近期时间。
        let now = Utc::now();
        let diff = now.signed_duration_since(cp.created_at);
        assert!(diff.num_seconds().abs() < 5);
    }
}
