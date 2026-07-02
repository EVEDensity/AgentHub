//! 消息段压缩：把最旧的 N-keep_recent 条消息合并成一个 [`SummarySegment`]。
//!
//! 算法：
//! 1. 计算总消息数与总 token；若都未达阈值 → 不压缩，原样返回。
//! 2. 触发原因：token 超预算优先（`token_budget`），否则消息数（`message_count`）。
//! 3. 保留最近 `keep_recent` 条，其余按序拼接成结构化摘要（v1 占位；
//!    真实 LLM 摘要由 summarization-service 经 NATS 异步生成）。
//! 4. token 估算用 [`estimate_tokens`](crate::types::estimate_tokens)（字符数/比率）。
//!
//! 注意：v1 摘要是结构化拼接（保留全部信息），token 不一定显著降低——
//! 真实降 token 由 summarization-service 的 LLM 摘要替换 `summary.content` 后实现。
//! 本核心负责"决定压缩什么 + 生成结构"，是性能热点；LLM 调用是 IO 密集，
//! 留给 Python 侧。

use crate::types::{estimate_tokens, CompactResult, CompactionConfig, Message, SummarySegment};

/// 对消息列表执行压缩。
pub fn compact_messages(messages: &[Message], config: &CompactionConfig) -> CompactResult {
    let total = messages.len();
    let tokens_before: usize = messages.iter().map(|m| m.token_count).sum();

    // 1. 判定是否需要压缩。
    let trigger_reason = if tokens_before >= config.max_tokens {
        Some("token_budget".to_string())
    } else if total >= config.compact_trigger_messages {
        Some("message_count".to_string())
    } else {
        None
    };

    let trigger = match trigger_reason.as_ref() {
        Some(t) => t.clone(),
        None => {
            return CompactResult {
                compacted: false,
                trigger_reason: None,
                summary: None,
                retained: messages.to_vec(),
                compacted_count: 0,
                tokens_before,
                tokens_after: tokens_before,
                token_reduction: 0,
            };
        }
    };

    // 2. 计算压缩范围。
    let keep_recent = config.keep_recent.min(total);
    let compact_count = total.saturating_sub(keep_recent);
    if compact_count == 0 {
        return CompactResult {
            compacted: false,
            trigger_reason: Some(trigger),
            summary: None,
            retained: messages.to_vec(),
            compacted_count: 0,
            tokens_before,
            tokens_after: tokens_before,
            token_reduction: 0,
        };
    }

    // 3. 切分：旧消息压缩，近期消息保留。
    let to_compact = &messages[0..compact_count];
    let retained = messages[compact_count..].to_vec();

    let summary = build_summary_segment(to_compact, config);
    let tokens_after =
        summary.token_count + retained.iter().map(|m| m.token_count).sum::<usize>();

    CompactResult {
        compacted: true,
        trigger_reason: Some(trigger),
        summary: Some(summary),
        retained,
        compacted_count: compact_count,
        tokens_before,
        tokens_after,
        token_reduction: tokens_before.saturating_sub(tokens_after),
    }
}

/// 构造摘要段（pub 供 checkpoint 复用）。
/// v1 用结构化拼接：header + 每条消息的 `[role]: content`。
pub fn build_summary_segment(messages: &[Message], config: &CompactionConfig) -> SummarySegment {
    let mut content = String::new();
    if config.summary_header && !messages.is_empty() {
        let start = messages.first().map(|m| m.sequence).unwrap_or(0);
        let end = messages.last().map(|m| m.sequence).unwrap_or(0);
        content.push_str(&format!(
            "[Compacted summary: {} messages, seq {}-{}]\n",
            messages.len(),
            start,
            end
        ));
    }
    for m in messages {
        content.push_str(&format!("[{}]: {}\n", m.role.as_str(), m.content));
    }
    // 去掉末尾多余换行。
    if content.ends_with('\n') {
        content.pop();
    }
    let token_count = estimate_tokens(&content, config);

    SummarySegment {
        content,
        token_count,
        covered_sequence_start: messages.first().map(|m| m.sequence).unwrap_or(0),
        covered_sequence_end: messages.last().map(|m| m.sequence).unwrap_or(0),
        covered_message_count: messages.len(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::MessageRole;

    fn msg(seq: u64, content: &str) -> Message {
        Message {
            sequence: seq,
            role: MessageRole::User,
            content: content.into(),
            token_count: estimate_tokens(content, &CompactionConfig::default()),
            timestamp: None,
        }
    }

    fn cfg(trigger: usize, keep: usize) -> CompactionConfig {
        CompactionConfig {
            compact_trigger_messages: trigger,
            keep_recent: keep,
            ..Default::default()
        }
    }

    #[test]
    fn no_compaction_below_threshold() {
        let msgs = vec![msg(1, "hi"), msg(2, "there")];
        let r = compact_messages(&msgs, &cfg(40, 10));
        assert!(!r.compacted);
        assert!(r.trigger_reason.is_none());
        assert_eq!(r.retained.len(), 2);
        assert_eq!(r.compacted_count, 0);
    }

    #[test]
    fn compaction_triggered_by_message_count() {
        let msgs: Vec<Message> = (1..=50).map(|i| msg(i, "x")).collect();
        let r = compact_messages(&msgs, &cfg(40, 10));
        assert!(r.compacted);
        assert_eq!(r.trigger_reason.as_deref(), Some("message_count"));
        assert_eq!(r.compacted_count, 40);
        assert_eq!(r.retained.len(), 10);
        assert_eq!(r.retained[0].sequence, 41); // 保留 seq 41-50
        let s = r.summary.unwrap();
        assert_eq!(s.covered_message_count, 40);
        assert_eq!(s.covered_sequence_start, 1);
        assert_eq!(s.covered_sequence_end, 40);
    }

    #[test]
    fn compaction_triggered_by_token_budget() {
        // 消息数未达 40，但 token 超预算。
        let config = CompactionConfig {
            compact_trigger_messages: 40,
            max_tokens: 10,
            keep_recent: 1,
            ..Default::default()
        };
        let msgs: Vec<Message> = (1..=5)
            .map(|i| Message {
                sequence: i,
                role: MessageRole::User,
                content: "some long content".into(),
                token_count: 5,
                timestamp: None,
            })
            .collect();
        let r = compact_messages(&msgs, &config);
        assert!(r.compacted);
        assert_eq!(r.trigger_reason.as_deref(), Some("token_budget"));
        assert_eq!(r.compacted_count, 4);
        assert_eq!(r.retained.len(), 1);
    }

    #[test]
    fn keep_recent_exceeds_total_compacts_nothing() {
        // total=5, keep_recent=10 → compact_count=0，即使达阈值也不压缩。
        let msgs: Vec<Message> = (1..=5).map(|i| msg(i, "x")).collect();
        let r = compact_messages(&msgs, &cfg(1, 10));
        // 触发但无可压缩。
        assert!(!r.compacted);
        assert_eq!(r.retained.len(), 5);
    }

    #[test]
    fn summary_segment_has_header_and_roles() {
        let msgs = vec![msg(1, "hello"), msg(2, "world")];
        let s = build_summary_segment(&msgs, &CompactionConfig::default());
        assert!(s.content.contains("Compacted summary: 2 messages"));
        assert!(s.content.contains("[user]: hello"));
        assert!(s.content.contains("[user]: world"));
        assert!(s.token_count >= 1);
    }

    #[test]
    fn empty_messages_compact_to_nothing() {
        let r = compact_messages(&[], &cfg(40, 10));
        assert!(!r.compacted);
        assert!(r.retained.is_empty());
    }

    #[test]
    fn token_reduction_non_negative() {
        let msgs: Vec<Message> = (1..=50).map(|i| msg(i, "x")).collect();
        let r = compact_messages(&msgs, &cfg(40, 10));
        // tokens_after 可能 > tokens_before（摘要拼接可能更长），reduction 用 saturating_sub。
        assert!(r.token_reduction == r.tokens_before.saturating_sub(r.tokens_after));
    }
}
