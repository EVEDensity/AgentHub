//! NATS 适配器：订阅 `agenthub.memory.compact.requested`，发布 `memory.compact.completed`。
//!
//! 事件流：
//! ```text
//!   agenthub.memory.compact.requested (NATS)
//!        │  EventEnvelope { payload: { messages: [...], config?: {...} } }
//!        ▼
//!   NatsAdapter::spawn_subscription
//!        │  解析 envelope → messages + 可选 config
//!        │  core.compact_with(messages, config) → CompactResult
//!        ▼
//!   agenthub.memory.audit (NATS)
//!        EventEnvelope { event_type: "memory.compact.completed", payload: CompactResult }
//! ```

use std::sync::Arc;

use async_nats::{Client, Subscriber};
use chrono::Utc;
use futures::StreamExt;
use tracing::{error, info, warn};

use platform_events::{EventEnvelope, Priority, Producer, Routing};

use crate::core::MemorySegmentCore;
use crate::types::{CompactionConfig, Message};

/// NATS subject 常量（与 Go eventbus 常量对齐）。
pub const MEMORY_COMPACT_REQUESTED_SUBJECT: &str = "agenthub.memory.compact.requested";
pub const MEMORY_AUDIT_SUBJECT: &str = "agenthub.memory.audit";

/// 事件类型常量。
pub const EVENT_MEMORY_COMPACT_COMPLETED: &str = "memory.compact.completed";

/// NATS 适配器。
pub struct NatsAdapter {
    client: Option<Client>,
}

impl NatsAdapter {
    pub fn new() -> Self {
        Self { client: None }
    }

    pub async fn connect(&mut self, url: &str) -> Result<(), Box<dyn std::error::Error>> {
        let client = async_nats::connect(url).await?;
        self.client = Some(client);
        Ok(())
    }

    pub fn is_connected(&self) -> bool {
        self.client.is_some()
    }

    /// 启动订阅循环。
    pub async fn spawn_subscription(
        self: Arc<Self>,
        core: Arc<MemorySegmentCore>,
    ) -> Result<(), async_nats::Error> {
        let client = match &self.client {
            Some(c) => c.clone(),
            None => {
                error!("cannot spawn subscription: NATS not connected");
                return Err("nats client missing".into());
            }
        };

        let mut sub: Subscriber = client
            .subscribe(MEMORY_COMPACT_REQUESTED_SUBJECT)
            .await?;
        info!(
            subject = MEMORY_COMPACT_REQUESTED_SUBJECT,
            "subscribed to memory compact requests"
        );

        tokio::spawn(async move {
            while let Some(msg) = sub.next().await {
                let env: EventEnvelope = match serde_json::from_slice(&msg.payload) {
                    Ok(e) => e,
                    Err(e) => {
                        warn!(error = %e, "failed to parse memory envelope, skipping");
                        continue;
                    }
                };

                let (messages, config) = match envelope_to_compact_request(&env) {
                    Some(v) => v,
                    None => {
                        warn!(
                            event_id = %env.event_id,
                            "envelope missing messages field, skipping"
                        );
                        continue;
                    }
                };

                info!(
                    event_id = %env.event_id,
                    message_count = messages.len(),
                    "processing memory compact request"
                );

                let result = core.compact_with(&messages, &config).await;

                let audit_env = build_audit_envelope(&env, &result);
                if let Err(e) =
                    publish_envelope(&client, MEMORY_AUDIT_SUBJECT, &audit_env).await
                {
                    error!(error = %e, "failed to publish memory.compact.completed");
                }

                info!(
                    event_id = %env.event_id,
                    compacted = result.compacted,
                    compacted_count = result.compacted_count,
                    token_reduction = result.token_reduction,
                    "memory compact request processed"
                );
            }
            info!("memory compact subscription ended");
        });

        Ok(())
    }

    pub async fn close(self) {
        drop(self.client);
    }
}

/// 把 EventEnvelope payload 投影为 `(messages, config)`。
/// payload 必须含 `messages` 数组；`config` 可选，缺失用默认。
fn envelope_to_compact_request(env: &EventEnvelope) -> Option<(Vec<Message>, CompactionConfig)> {
    let messages_val = env.payload.get("messages")?;
    let messages: Vec<Message> = serde_json::from_value(messages_val.clone()).ok()?;
    // config 可选：从 payload["config"] 解析，缺失用默认。
    let config = env
        .payload
        .get("config")
        .and_then(|v| serde_json::from_value::<CompactionConfigHelper>(v.clone()).ok())
        .map(Into::into)
        .unwrap_or_default();
    Some((messages, config))
}

/// 配置反序列化辅助（CompactionConfig 未实现 Deserialize，用 helper 中转）。
#[derive(Debug, serde::Deserialize)]
struct CompactionConfigHelper {
    #[serde(default = "default_trigger")]
    compact_trigger_messages: usize,
    #[serde(default = "default_max_tokens")]
    max_tokens: usize,
    #[serde(default = "default_keep_recent")]
    keep_recent: usize,
    #[serde(default = "default_chars_per_token")]
    chars_per_token: f64,
    #[serde(default = "default_true")]
    summary_header: bool,
}

fn default_trigger() -> usize {
    40
}
fn default_max_tokens() -> usize {
    32_000
}
fn default_keep_recent() -> usize {
    10
}
fn default_chars_per_token() -> f64 {
    4.0
}
fn default_true() -> bool {
    true
}

impl From<CompactionConfigHelper> for CompactionConfig {
    fn from(h: CompactionConfigHelper) -> Self {
        CompactionConfig {
            compact_trigger_messages: h.compact_trigger_messages,
            max_tokens: h.max_tokens,
            keep_recent: h.keep_recent,
            chars_per_token: h.chars_per_token,
            summary_header: h.summary_header,
        }
    }
}

/// 构造 `memory.compact.completed` 事件信封。
fn build_audit_envelope(orig: &EventEnvelope, result: &crate::types::CompactResult) -> EventEnvelope {
    let mut payload = std::collections::BTreeMap::new();
    payload.insert("compacted".into(), serde_json::json!(result.compacted));
    payload.insert(
        "trigger_reason".into(),
        serde_json::json!(result.trigger_reason),
    );
    payload.insert("summary".into(), serde_json::json!(result.summary));
    payload.insert("retained".into(), serde_json::json!(result.retained));
    payload.insert(
        "compacted_count".into(),
        serde_json::json!(result.compacted_count),
    );
    payload.insert("tokens_before".into(), serde_json::json!(result.tokens_before));
    payload.insert("tokens_after".into(), serde_json::json!(result.tokens_after));
    payload.insert(
        "token_reduction".into(),
        serde_json::json!(result.token_reduction),
    );

    EventEnvelope {
        event_id: format!("memory-compact-{}", orig.event_id),
        event_type: EVENT_MEMORY_COMPACT_COMPLETED.into(),
        event_version: 1,
        occurred_at: Utc::now(),
        trace_id: orig.trace_id.clone(),
        tenant_id: orig.tenant_id.clone(),
        session_id: orig.session_id.clone(),
        message_id: Some(orig.event_id.clone()),
        actor_id: orig.actor_id.clone(),
        producer: Producer {
            service: "memory-segment-core".into(),
            instance: std::env::var("HOSTNAME").unwrap_or_else(|_| "local".into()),
            region: None,
        },
        routing: Some(Routing {
            channel: Some("memory".into()),
            partition_key: Some(orig.session_id.clone()),
            priority: Some(Priority::Normal),
        }),
        payload,
    }
}

/// 发布事件信封到 NATS。
async fn publish_envelope(
    client: &Client,
    subject: &str,
    env: &EventEnvelope,
) -> Result<(), Box<dyn std::error::Error>> {
    let payload = serde_json::to_vec(env)?;
    client.publish(subject.to_string(), payload.into()).await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::MessageRole;
    use chrono::Utc;
    use platform_events::{EventEnvelope, Priority, Producer, Routing};
    use serde_json::json;
    use std::collections::BTreeMap;

    fn env_with_messages(messages: &[(&str, &str)], config: Option<serde_json::Value>) -> EventEnvelope {
        let mut payload = BTreeMap::new();
        let msgs: Vec<serde_json::Value> = messages
            .iter()
            .enumerate()
            .map(|(i, (role, content))| {
                json!({
                    "sequence": i as u64 + 1,
                    "role": role,
                    "content": content,
                    "token_count": 5,
                })
            })
            .collect();
        payload.insert("messages".into(), json!(msgs));
        if let Some(c) = config {
            payload.insert("config".into(), c);
        }
        EventEnvelope {
            event_id: "e1".into(),
            event_type: "memory.compact.requested".into(),
            event_version: 1,
            occurred_at: Utc::now(),
            trace_id: "tr".into(),
            tenant_id: "t".into(),
            session_id: "sess-1".into(),
            message_id: Some("m".into()),
            actor_id: None,
            producer: Producer {
                service: "test".into(),
                instance: "local".into(),
                region: None,
            },
            routing: Some(Routing {
                channel: Some("memory".into()),
                partition_key: Some("sess-1".into()),
                priority: Some(Priority::Normal),
            }),
            payload,
        }
    }

    #[test]
    fn envelope_with_messages_parses() {
        let env = env_with_messages(&[("user", "hi"), ("assistant", "hello")], None);
        let (msgs, config) = envelope_to_compact_request(&env).unwrap();
        assert_eq!(msgs.len(), 2);
        assert_eq!(msgs[0].role, MessageRole::User);
        assert_eq!(msgs[1].role, MessageRole::Assistant);
        assert_eq!(config.compact_trigger_messages, 40); // 默认
    }

    #[test]
    fn envelope_with_custom_config_parses() {
        let cfg = json!({"compact_trigger_messages": 10, "keep_recent": 3});
        let env = env_with_messages(&[("user", "hi")], Some(cfg));
        let (_, config) = envelope_to_compact_request(&env).unwrap();
        assert_eq!(config.compact_trigger_messages, 10);
        assert_eq!(config.keep_recent, 3);
    }

    #[test]
    fn envelope_missing_messages_returns_none() {
        let mut payload = BTreeMap::new();
        payload.insert("other".into(), json!("x"));
        let env = EventEnvelope {
            event_id: "e1".into(),
            event_type: "test".into(),
            event_version: 1,
            occurred_at: Utc::now(),
            trace_id: "tr".into(),
            tenant_id: "t".into(),
            session_id: "s".into(),
            message_id: None,
            actor_id: None,
            producer: Producer {
                service: "test".into(),
                instance: "local".into(),
                region: None,
            },
            routing: None,
            payload,
        };
        assert!(envelope_to_compact_request(&env).is_none());
    }

    #[test]
    fn audit_envelope_carries_compact_result() {
        let env = env_with_messages(&[("user", "hi")], None);
        let result = crate::compact::compact_messages(
            &[Message::new(1, MessageRole::User, "hi")],
            &CompactionConfig::default(),
        );
        let audit = build_audit_envelope(&env, &result);
        assert_eq!(audit.event_type, EVENT_MEMORY_COMPACT_COMPLETED);
        assert_eq!(audit.producer.service, "memory-segment-core");
        assert_eq!(
            audit.payload.get("compacted").and_then(|v| v.as_bool()),
            Some(false)
        );
    }
}
