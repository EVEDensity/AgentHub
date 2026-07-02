//! 窗口裁剪：直接丢弃超出 `keep_recent` 的旧消息（不生成摘要）。
//!
//! 用于硬性 token 上限场景——当不关心历史内容、只需保留最近消息时使用。
//! 与 [`compact`](crate::compact) 的区别：compact 生成摘要段保留信息，
//! prune 直接丢弃。

use crate::types::{CompactionConfig, Message, PruneResult};

/// 裁剪消息列表，只保留最近 `keep_recent` 条。
pub fn prune_messages(messages: &[Message], config: &CompactionConfig) -> PruneResult {
    let total = messages.len();
    let tokens_before: usize = messages.iter().map(|m| m.token_count).sum();
    let keep = config.keep_recent.min(total);
    let prune_count = total.saturating_sub(keep);

    let pruned = messages[0..prune_count].to_vec();
    let retained = messages[prune_count..].to_vec();
    let tokens_after: usize = retained.iter().map(|m| m.token_count).sum();

    PruneResult {
        pruned,
        retained,
        pruned_count: prune_count,
        tokens_before,
        tokens_after,
        token_reduction: tokens_before.saturating_sub(tokens_after),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{estimate_tokens, MessageRole};

    fn msg(seq: u64, content: &str) -> Message {
        Message {
            sequence: seq,
            role: MessageRole::User,
            content: content.into(),
            token_count: estimate_tokens(content, &CompactionConfig::default()),
            timestamp: None,
        }
    }

    #[test]
    fn prune_keeps_recent_messages() {
        let msgs: Vec<Message> = (1..=20).map(|i| msg(i, "x")).collect();
        let config = CompactionConfig {
            keep_recent: 5,
            ..Default::default()
        };
        let r = prune_messages(&msgs, &config);
        assert_eq!(r.pruned_count, 15);
        assert_eq!(r.retained.len(), 5);
        assert_eq!(r.retained[0].sequence, 16);
        assert_eq!(r.retained[4].sequence, 20);
        assert_eq!(r.pruned[0].sequence, 1);
    }

    #[test]
    fn prune_when_fewer_than_keep_keeps_all() {
        let msgs = vec![msg(1, "a"), msg(2, "b")];
        let config = CompactionConfig {
            keep_recent: 10,
            ..Default::default()
        };
        let r = prune_messages(&msgs, &config);
        assert_eq!(r.pruned_count, 0);
        assert_eq!(r.retained.len(), 2);
    }

    #[test]
    fn prune_empty_is_noop() {
        let r = prune_messages(&[], &CompactionConfig::default());
        assert_eq!(r.pruned_count, 0);
        assert!(r.retained.is_empty());
    }

    #[test]
    fn prune_token_reduction_correct() {
        let msgs: Vec<Message> = (1..=10)
            .map(|i| Message {
                sequence: i,
                role: MessageRole::User,
                content: "x".into(),
                token_count: 3,
                timestamp: None,
            })
            .collect();
        let config = CompactionConfig {
            keep_recent: 3,
            ..Default::default()
        };
        let r = prune_messages(&msgs, &config);
        assert_eq!(r.tokens_before, 30);
        assert_eq!(r.tokens_after, 9);
        assert_eq!(r.token_reduction, 21);
    }
}
