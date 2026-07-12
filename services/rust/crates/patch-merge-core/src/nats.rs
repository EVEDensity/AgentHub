//! NATS 适配器：订阅 `agenthub.patch.merge.requested`，发布 `patch.merge.completed`。
//!
//! 事件流：
//! ```text
//!   agenthub.patch.merge.requested (NATS)
//!        │  EventEnvelope { payload: { base, ours, theirs, trace_id? } }
//!        ▼
//!   NatsAdapter::spawn_subscription
//!        │  解析 envelope → MergeRequest
//!        │  core.merge(req) → MergeResult
//!        ▼
//!   agenthub.patch.audit (NATS)
//!        EventEnvelope { event_type: "patch.merge.completed", payload: MergeResult }
//! ```
//!
//! 用 core NATS 订阅接收事件；持久化由上游 JetStream 负责，职责分离。

use std::sync::Arc;

use async_nats::{Client, Subscriber};
use chrono::Utc;
use futures::StreamExt;
use tracing::{error, info, warn};

use platform_events::{EventEnvelope, Priority, Producer, Routing};

use crate::core::PatchMergeCore;
use crate::types::MergeRequest;

/// NATS subject 常量（与 Go eventbus 常量对齐）。
pub const PATCH_MERGE_REQUESTED_SUBJECT: &str = "agenthub.patch.merge.requested";
pub const PATCH_AUDIT_SUBJECT: &str = "agenthub.patch.audit";

/// 事件类型常量。
pub const EVENT_PATCH_MERGE_COMPLETED: &str = "patch.merge.completed";

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

    /// 启动订阅循环。返回 `Ok` 表示订阅已建立；`Err` 表示订阅失败。
    pub async fn spawn_subscription(
        self: Arc<Self>,
        core: Arc<PatchMergeCore>,
    ) -> Result<(), async_nats::Error> {
        let client = match &self.client {
            Some(c) => c.clone(),
            None => {
                error!("cannot spawn subscription: NATS not connected");
                return Err("nats client missing".into());
            }
        };

        let mut sub: Subscriber = client
            .subscribe(PATCH_MERGE_REQUESTED_SUBJECT)
            .await?;
        info!(
            subject = PATCH_MERGE_REQUESTED_SUBJECT,
            "subscribed to patch-merge requests"
        );

        tokio::spawn(async move {
            while let Some(msg) = sub.next().await {
                let env: EventEnvelope = match serde_json::from_slice(&msg.payload) {
                    Ok(e) => e,
                    Err(e) => {
                        warn!(error = %e, "failed to parse patch-merge envelope, skipping");
                        continue;
                    }
                };

                let req = match envelope_to_merge_request(&env) {
                    Some(r) => r,
                    None => {
                        warn!(
                            event_id = %env.event_id,
                            "envelope missing merge payload fields, skipping"
                        );
                        continue;
                    }
                };

                info!(
                    event_id = %env.event_id,
                    base_lines = req.base.lines().count(),
                    "processing patch-merge request"
                );

                let result = match core.merge(&req).await {
                    Ok(r) => r,
                    Err(e) => {
                        warn!(error = %e, "merge rejected");
                        continue;
                    }
                };

                let audit_env = build_audit_envelope(&env, &result);
                if let Err(e) =
                    publish_envelope(&client, PATCH_AUDIT_SUBJECT, &audit_env).await
                {
                    error!(error = %e, "failed to publish patch.merge.completed");
                }

                info!(
                    event_id = %env.event_id,
                    has_conflicts = result.has_conflicts,
                    conflict_score = result.conflict_score,
                    conflicts = result.conflicts.len(),
                    "patch-merge request processed"
                );
            }
            info!("patch-merge subscription ended");
        });

        Ok(())
    }

    pub async fn close(self) {
        drop(self.client);
    }
}

/// 把 EventEnvelope payload 投影为 [`MergeRequest`]。
/// 要求 payload 含 base/ours/theirs 三个字符串字段；缺失返回 None。
fn envelope_to_merge_request(env: &EventEnvelope) -> Option<MergeRequest> {
    let get_str = |key: &str| env.payload.get(key).and_then(|v| v.as_str()).map(String::from);
    Some(MergeRequest {
        base: get_str("base")?,
        ours: get_str("ours")?,
        theirs: get_str("theirs")?,
        trace_id: Some(env.trace_id.clone()),
    })
}

/// 构造 `patch.merge.completed` 事件信封。
fn build_audit_envelope(orig: &EventEnvelope, result: &crate::types::MergeResult) -> EventEnvelope {
    let mut payload = std::collections::BTreeMap::new();
    payload.insert("merged_text".into(), serde_json::json!(result.merged_text));
    payload.insert("has_conflicts".into(), serde_json::json!(result.has_conflicts));
    payload.insert("conflict_score".into(), serde_json::json!(result.conflict_score));
    payload.insert("conflicts".into(), serde_json::json!(result.conflicts));
    payload.insert("base_lines".into(), serde_json::json!(result.base_lines));
    payload.insert("merged_lines".into(), serde_json::json!(result.merged_lines));

    EventEnvelope {
        event_id: format!("patch-merge-{}", orig.event_id),
        event_type: EVENT_PATCH_MERGE_COMPLETED.into(),
        event_version: 1,
        occurred_at: Utc::now(),
        trace_id: orig.trace_id.clone(),
        tenant_id: orig.tenant_id.clone(),
        session_id: orig.session_id.clone(),
        message_id: Some(orig.event_id.clone()),
        actor_id: orig.actor_id.clone(),
        producer: Producer {
            service: "patch-merge-core".into(),
            instance: std::env::var("HOSTNAME").unwrap_or_else(|_| "local".into()),
            region: None,
        },
        routing: Some(Routing {
            channel: Some("patch".into()),
            partition_key: Some(orig.trace_id.clone()),
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

    fn env_with_merge(base: &str, ours: &str, theirs: &str) -> EventEnvelope {
        let mut payload = BTreeMap::new();
        payload.insert("base".into(), json!(base));
        payload.insert("ours".into(), json!(ours));
        payload.insert("theirs".into(), json!(theirs));
        EventEnvelope {
            event_id: "e1".into(),
            event_type: "patch.merge.requested".into(),
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
                channel: Some("patch".into()),
                partition_key: Some("p".into()),
                priority: Some(Priority::Normal),
            }),
            payload,
        }
    }

    #[test]
    fn envelope_with_full_payload_maps_to_request() {
        let env = env_with_merge("a\nb", "A\nb", "a\nB");
        let req = envelope_to_merge_request(&env).unwrap();
        assert_eq!(req.base, "a\nb");
        assert_eq!(req.ours, "A\nb");
        assert_eq!(req.theirs, "a\nB");
        assert_eq!(req.trace_id.as_deref(), Some("tr"));
    }

    #[test]
    fn envelope_missing_field_returns_none() {
        let mut env = env_with_merge("a", "b", "c");
        env.payload.remove("theirs");
        assert!(envelope_to_merge_request(&env).is_none());
    }

    #[test]
    fn envelope_non_string_field_returns_none() {
        let mut env = env_with_merge("a", "b", "c");
        env.payload.insert("base".into(), json!(123));
        assert!(envelope_to_merge_request(&env).is_none());
    }

    #[test]
    fn audit_envelope_carries_merge_result() {
        let env = env_with_merge("a\nb\nc", "a\nB1\nc", "a\nB2\nc");
        let result = crate::merge::three_way_merge("a\nb\nc", "a\nB1\nc", "a\nB2\nc").into_result();
        let audit = build_audit_envelope(&env, &result);
        assert_eq!(audit.event_type, EVENT_PATCH_MERGE_COMPLETED);
        assert_eq!(audit.producer.service, "patch-merge-core");
        assert_eq!(
            audit.payload.get("has_conflicts").and_then(|v| v.as_bool()),
            Some(true)
        );
        assert!(audit.payload.get("merged_text").is_some());
    }
}
