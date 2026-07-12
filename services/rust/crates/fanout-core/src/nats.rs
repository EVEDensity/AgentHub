//! NATS 适配器：订阅 `agenthub.fanout.events`，发布 `fanout.event.delivered`。
//!
//! 事件流：
//! ```text
//!   agenthub.fanout.events (JetStream)
//!        │  EventEnvelope { routing.channel, routing.partition_key, payload }
//!        ▼
//!   NatsAdapter::spawn_subscription
//!        │  解析 envelope → FanoutEvent
//!        │  core.route(event) → DeliveryReceipt
//!        ▼
//!   agenthub.fanout.audit (JetStream)
//!        EventEnvelope { event_type: "fanout.event.delivered", payload: DeliveryReceipt }
//! ```
//!
//! 注意：fanout-core 用 core NATS 订阅接收事件（JetStream publish 会同时
//! 投递给 core 订阅者）；持久化由 stream-delivery-service 的 Redis Streams
//! 负责，职责分离。

use std::sync::Arc;

use async_nats::{Client, Subscriber};
use chrono::Utc;
use futures::StreamExt;
use tracing::{error, info, warn};

use platform_events::{EventEnvelope, Priority, Producer, Routing};

use crate::core::FanoutCore;
use crate::types::FanoutEvent;

/// NATS subject 常量（与 Go eventbus 常量对齐）。
pub const FANOUT_EVENTS_SUBJECT: &str = "agenthub.fanout.events";
pub const FANOUT_AUDIT_SUBJECT: &str = "agenthub.fanout.audit";

/// 事件类型常量。
pub const EVENT_FANOUT_DELIVERED: &str = "fanout.event.delivered";

/// NATS 适配器：持有连接，不持有 subscriber（由 spawned task 持有）。
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

    /// 是否已连接。
    pub fn is_connected(&self) -> bool {
        self.client.is_some()
    }

    /// 启动订阅循环。返回 `Ok` 表示订阅已建立（不是消息处理结束）；
    /// `Err` 表示订阅失败（调用方应记录并继续以 HTTP-only 模式运行）。
    pub async fn spawn_subscription(
        self: Arc<Self>,
        core: Arc<FanoutCore>,
    ) -> Result<(), async_nats::Error> {
        let client = match &self.client {
            Some(c) => c.clone(),
            None => {
                error!("cannot spawn subscription: NATS not connected");
                return Err("nats client missing".into());
            }
        };

        let mut sub: Subscriber = client.subscribe(FANOUT_EVENTS_SUBJECT).await?;
        info!(subject = FANOUT_EVENTS_SUBJECT, "subscribed to fanout events");

        tokio::spawn(async move {
            while let Some(msg) = sub.next().await {
                let env: EventEnvelope = match serde_json::from_slice(&msg.payload) {
                    Ok(e) => e,
                    Err(e) => {
                        warn!(error = %e, "failed to parse fanout envelope, skipping");
                        continue;
                    }
                };

                let event = match envelope_to_event(&env) {
                    Some(e) => e,
                    None => {
                        // 无 routing.channel 的事件，fanout-core 不处理。
                        continue;
                    }
                };

                info!(
                    event_id = %event.event_id,
                    channel = %event.channel,
                    partition_key = %event.partition_key,
                    "processing fanout event"
                );

                let receipt = core.route(event).await;

                let audit_env = build_audit_envelope(&env, &receipt);
                if let Err(e) = publish_envelope(&client, FANOUT_AUDIT_SUBJECT, &audit_env).await {
                    error!(error = %e, "failed to publish fanout.event.delivered");
                }

                info!(
                    event_id = %receipt.event_id,
                    channel = %receipt.channel,
                    delivered = receipt.delivered,
                    dropped = receipt.dropped,
                    elapsed_ms = receipt.elapsed_ms,
                    "fanout event routed"
                );
            }
            info!("fanout events subscription ended");
        });

        Ok(())
    }

    pub async fn close(self) {
        // async_nats Client 在 drop 时自动断开。
        drop(self.client);
    }
}

/// 把 EventEnvelope 投影为 FanoutEvent。
/// 仅处理带 `routing.channel`（非空）的事件；其余返回 None。
fn envelope_to_event(env: &EventEnvelope) -> Option<FanoutEvent> {
    let routing = env.routing.as_ref()?;
    let channel = routing.channel.as_deref().filter(|c| !c.is_empty())?;
    let partition_key = routing
        .partition_key
        .clone()
        .filter(|k| !k.is_empty())
        .unwrap_or_else(|| env.session_id.clone());
    let payload = serde_json::to_value(&env.payload).unwrap_or(serde_json::Value::Null);
    Some(FanoutEvent {
        event_id: env.event_id.clone(),
        event_type: env.event_type.clone(),
        channel: channel.to_string(),
        partition_key,
        partition: 0, // 由 FanoutCore.route 计算。
        tenant_id: env.tenant_id.clone(),
        session_id: env.session_id.clone(),
        trace_id: env.trace_id.clone(),
        occurred_at: env.occurred_at,
        payload,
    })
}

/// 构造 `fanout.event.delivered` 事件信封。
fn build_audit_envelope(orig: &EventEnvelope, receipt: &crate::types::DeliveryReceipt) -> EventEnvelope {
    let mut payload = std::collections::BTreeMap::new();
    payload.insert("event_id".into(), serde_json::json!(receipt.event_id));
    payload.insert("channel".into(), serde_json::json!(receipt.channel));
    payload.insert("partition".into(), serde_json::json!(receipt.partition));
    payload.insert(
        "subscriber_count".into(),
        serde_json::json!(receipt.subscriber_count),
    );
    payload.insert("delivered".into(), serde_json::json!(receipt.delivered));
    payload.insert("dropped".into(), serde_json::json!(receipt.dropped));
    payload.insert("elapsed_ms".into(), serde_json::json!(receipt.elapsed_ms));
    payload.insert("degraded".into(), serde_json::json!(receipt.degraded));

    EventEnvelope {
        event_id: format!("fanout-delivered-{}", receipt.event_id),
        event_type: EVENT_FANOUT_DELIVERED.into(),
        event_version: 1,
        occurred_at: Utc::now(),
        trace_id: orig.trace_id.clone(),
        tenant_id: orig.tenant_id.clone(),
        session_id: orig.session_id.clone(),
        message_id: Some(receipt.event_id.clone()),
        actor_id: orig.actor_id.clone(),
        producer: Producer {
            service: "fanout-core".into(),
            instance: std::env::var("HOSTNAME").unwrap_or_else(|_| "local".into()),
            region: None,
        },
        routing: Some(Routing {
            channel: Some(receipt.channel.clone()),
            partition_key: Some(receipt.event_id.clone()),
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
    // subject 需转 owned String：async-nats 0.33 的 publish 要求 ToSubject 满足 'static。
    client.publish(subject.to_string(), payload.into()).await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Utc;
    use platform_events::{EventEnvelope, Priority, Producer, Routing};
    use serde_json::json;
    use std::collections::BTreeMap;

    fn env_with_channel(channel: Option<&str>, partition_key: Option<&str>) -> EventEnvelope {
        let mut payload = BTreeMap::new();
        payload.insert("content".into(), json!("hello"));
        EventEnvelope {
            event_id: "e1".into(),
            event_type: "test.event".into(),
            event_version: 1,
            occurred_at: Utc::now(),
            trace_id: "tr".into(),
            tenant_id: "t".into(),
            session_id: "s".into(),
            message_id: Some("m".into()),
            actor_id: None,
            producer: Producer {
                service: "test".into(),
                instance: "local".into(),
                region: None,
            },
            routing: Some(Routing {
                channel: channel.map(String::from),
                partition_key: partition_key.map(String::from),
                priority: Some(Priority::Normal),
            }),
            payload,
        }
    }

    #[test]
    fn envelope_with_channel_maps_to_event() {
        let env = env_with_channel(Some("session"), Some("sess-123"));
        let e = envelope_to_event(&env).unwrap();
        assert_eq!(e.channel, "session");
        assert_eq!(e.partition_key, "sess-123");
        assert_eq!(e.event_id, "e1");
    }

    #[test]
    fn envelope_without_routing_returns_none() {
        let mut env = env_with_channel(Some("session"), None);
        env.routing = None;
        assert!(envelope_to_event(&env).is_none());
    }

    #[test]
    fn envelope_with_empty_channel_returns_none() {
        let env = env_with_channel(Some(""), None);
        assert!(envelope_to_event(&env).is_none());
    }

    #[test]
    fn envelope_with_none_channel_returns_none() {
        let env = env_with_channel(None, None);
        assert!(envelope_to_event(&env).is_none());
    }

    #[test]
    fn envelope_falls_back_to_session_id_for_partition_key() {
        let env = env_with_channel(Some("session"), None);
        let e = envelope_to_event(&env).unwrap();
        assert_eq!(e.partition_key, "s"); // env.session_id
    }

    #[test]
    fn envelope_empty_partition_key_falls_back_to_session_id() {
        let env = env_with_channel(Some("session"), Some(""));
        let e = envelope_to_event(&env).unwrap();
        assert_eq!(e.partition_key, "s"); // 回退到 session_id
    }

    #[test]
    fn audit_envelope_carries_receipt_fields() {
        let env = env_with_channel(Some("audit"), Some("tenant-a"));
        let receipt = crate::types::DeliveryReceipt {
            event_id: "evt-1".into(),
            channel: "audit".into(),
            partition: 3,
            subscriber_count: 5,
            delivered: 4,
            dropped: 1,
            elapsed_ms: 12,
            degraded: true,
        };
        let audit = build_audit_envelope(&env, &receipt);
        assert_eq!(audit.event_type, EVENT_FANOUT_DELIVERED);
        assert_eq!(
            audit.payload.get("delivered").and_then(|v| v.as_u64()),
            Some(4)
        );
        assert_eq!(
            audit.payload.get("dropped").and_then(|v| v.as_u64()),
            Some(1)
        );
        assert_eq!(
            audit.payload.get("partition").and_then(|v| v.as_u64()),
            Some(3)
        );
        assert_eq!(audit.producer.service, "fanout-core");
    }
}
