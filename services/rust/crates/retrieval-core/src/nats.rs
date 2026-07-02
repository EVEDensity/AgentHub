//! NATS 适配器：订阅 `retrieval.query.requested`，发布 `retrieval.fusion.completed`。
//!
//! 事件流：
//! ```text
//!   agenthub.retrieval.query (JetStream)
//!        │  EventEnvelope { event_type: "retrieval.query.requested", payload: {...} }
//!        ▼
//!   NatsAdapter::spawn_subscription
//!        │  解析 payload → RetrievalRequest
//!        │  core.retrieve(req) → FusionResult
//!        ▼
//!   agenthub.retrieval.fusion (JetStream)
//!        EventEnvelope { event_type: "retrieval.fusion.completed", payload: FusionResult }
//! ```

use std::sync::Arc;

use async_nats::{Client, Subscriber};
use chrono::Utc;
use futures::StreamExt;
use tracing::{error, info, warn};

use platform_events::EventEnvelope;

use crate::core::RetrievalCore;
use crate::types::RetrievalRequest;

/// NATS subject 常量（与 Go eventbus 对齐）。
pub const RETRIEVAL_QUERY_SUBJECT: &str = "agenthub.retrieval.query";
pub const RETRIEVAL_FUSION_SUBJECT: &str = "agenthub.retrieval.fusion";

/// 事件类型常量（与 Go events/envelope.go 对齐）。
pub const EVENT_RETRIEVAL_QUERY_REQUESTED: &str = "retrieval.query.requested";
pub const EVENT_RETRIEVAL_FUSION_COMPLETED: &str = "retrieval.fusion.completed";
pub const EVENT_RETRIEVAL_QUERY_COMPLETED: &str = "retrieval.query.completed";

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
        core: Arc<RetrievalCore>,
    ) -> Result<(), async_nats::Error> {
        let client = match &self.client {
            Some(c) => c.clone(),
            None => {
                error!("cannot spawn subscription: NATS not connected");
                return Err("nats client missing".into());
            }
        };

        let mut sub: Subscriber = client.subscribe(RETRIEVAL_QUERY_SUBJECT).await?;

        info!(subject = RETRIEVAL_QUERY_SUBJECT, "subscribed to retrieval queries");

        tokio::spawn(async move {
            while let Some(msg) = sub.next().await {
                let payload = match std::str::from_utf8(&msg.payload) {
                    Ok(s) => s,
                    Err(e) => {
                        warn!(error = %e, "retrieval query payload not utf8, skipping");
                        continue;
                    }
                };
                let env: EventEnvelope = match serde_json::from_str(payload) {
                    Ok(e) => e,
                    Err(e) => {
                        warn!(error = %e, "failed to parse retrieval query envelope, skipping");
                        continue;
                    }
                };

                if env.event_type != EVENT_RETRIEVAL_QUERY_REQUESTED {
                    continue;
                }

                let req = match parse_request(&env) {
                    Ok(r) => r,
                    Err(e) => {
                        warn!(error = %e, request_id = %env.event_id, "failed to parse retrieval request");
                        continue;
                    }
                };

                info!(
                    request_id = %req.request_id,
                    query = %req.query,
                    mode = %req.mode,
                    "processing retrieval request"
                );

                let result = core.retrieve(&req).await;

                // 发布 fusion.completed。
                let fusion_env = build_fusion_envelope(&env, &result);
                if let Err(e) = publish_envelope(&client, RETRIEVAL_FUSION_SUBJECT, &fusion_env).await {
                    error!(error = %e, "failed to publish fusion.completed");
                }

                // 发布 query.completed（统计摘要）。
                let query_completed = crate::types::QueryCompleted {
                    request_id: req.request_id.clone(),
                    candidate_count: result.candidates.len(),
                    qdrant_hits: result.qdrant_hits,
                    opensearch_hits: result.opensearch_hits,
                    elapsed_ms: result.elapsed_ms,
                    result_ref: None, // MinIO 上传由调用方或后续步骤完成。
                };
                let query_env = build_query_completed_envelope(&env, &query_completed);
                if let Err(e) = publish_envelope(&client, RETRIEVAL_FUSION_SUBJECT, &query_env).await {
                    error!(error = %e, "failed to publish query.completed");
                }

                info!(
                    request_id = %req.request_id,
                    elapsed_ms = result.elapsed_ms,
                    candidates = result.candidates.len(),
                    degraded = ?result.degraded,
                    "retrieval request completed"
                );
            }
            info!("retrieval query subscription ended");
        });

        Ok(())
    }

    pub async fn close(self) {
        // async_nats Client 在 drop 时自动断开。
        drop(self.client);
    }
}

/// 从事件信封解析检索请求。
fn parse_request(env: &EventEnvelope) -> Result<RetrievalRequest, serde_json::Error> {
    // payload 中提取 request_id / query / mode / knowledge_scope 等。
    let payload_json = serde_json::to_string(&env.payload)?;
    let mut req: RetrievalRequest = serde_json::from_str(&payload_json)?;
    // 从 envelope 顶层补全 tenant/session/trace。
    req.tenant_id = env.tenant_id.clone();
    req.session_id = env.session_id.clone();
    req.trace_id = env.trace_id.clone();
    // 若 payload 中没有 request_id，用 envelope 的 message_id / event_id。
    if req.request_id.is_empty() {
        req.request_id = env
            .message_id
            .clone()
            .unwrap_or_else(|| env.event_id.clone());
    }
    Ok(req)
}

/// 构造 `retrieval.fusion.completed` 事件信封。
fn build_fusion_envelope(orig: &EventEnvelope, result: &crate::types::FusionResult) -> EventEnvelope {
    EventEnvelope {
        event_id: format!("fusion-{}", result.request_id),
        event_type: EVENT_RETRIEVAL_FUSION_COMPLETED.to_string(),
        event_version: 1,
        occurred_at: Utc::now(),
        trace_id: orig.trace_id.clone(),
        tenant_id: orig.tenant_id.clone(),
        session_id: orig.session_id.clone(),
        message_id: Some(result.request_id.clone()),
        actor_id: orig.actor_id.clone(),
        producer: platform_events::Producer {
            service: "retrieval-core".into(),
            instance: std::env::var("HOSTNAME").unwrap_or_else(|_| "local".into()),
            region: None,
        },
        routing: Some(platform_events::Routing {
            channel: Some("retrieval".into()),
            partition_key: Some(orig.session_id.clone()),
            priority: Some(platform_events::Priority::Normal),
        }),
        payload: value_to_payload(serde_json::to_value(result).unwrap_or_default()),
    }
}

/// 构造 `retrieval.query.completed` 事件信封。
fn build_query_completed_envelope(
    orig: &EventEnvelope,
    completed: &crate::types::QueryCompleted,
) -> EventEnvelope {
    EventEnvelope {
        event_id: format!("retrieval-done-{}", completed.request_id),
        event_type: EVENT_RETRIEVAL_QUERY_COMPLETED.to_string(),
        event_version: 1,
        occurred_at: Utc::now(),
        trace_id: orig.trace_id.clone(),
        tenant_id: orig.tenant_id.clone(),
        session_id: orig.session_id.clone(),
        message_id: Some(completed.request_id.clone()),
        actor_id: orig.actor_id.clone(),
        producer: platform_events::Producer {
            service: "retrieval-core".into(),
            instance: std::env::var("HOSTNAME").unwrap_or_else(|_| "local".into()),
            region: None,
        },
        routing: Some(platform_events::Routing {
            channel: Some("retrieval".into()),
            partition_key: Some(orig.session_id.clone()),
            priority: Some(platform_events::Priority::Normal),
        }),
        payload: value_to_payload(serde_json::to_value(completed).unwrap_or_default()),
    }
}

/// 把 `serde_json::Value` 转为 `BTreeMap<String, Value>`（EventEnvelope.payload 的类型）。
/// 非 Object 的 Value 退化为空 map。
fn value_to_payload(v: serde_json::Value) -> std::collections::BTreeMap<String, serde_json::Value> {
    match v {
        serde_json::Value::Object(map) => map.into_iter().collect(),
        _ => std::collections::BTreeMap::new(),
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
    use std::collections::BTreeMap;

    #[test]
    fn parse_request_extracts_fields() {
        let mut payload = BTreeMap::new();
        payload.insert("request_id".to_string(), serde_json::json!("req-123"));
        payload.insert("query".to_string(), serde_json::json!("how to build gateway"));
        payload.insert("mode".to_string(), serde_json::json!("deepsearch"));
        payload.insert(
            "knowledge_scope".to_string(),
            serde_json::json!(["docs", "code"]),
        );

        let env = EventEnvelope {
            event_id: "evt-1".into(),
            event_type: EVENT_RETRIEVAL_QUERY_REQUESTED.into(),
            event_version: 1,
            occurred_at: Utc::now(),
            trace_id: "trace-1".into(),
            tenant_id: "tenant-a".into(),
            session_id: "sess-1".into(),
            message_id: Some("msg-1".into()),
            actor_id: None,
            producer: platform_events::Producer {
                service: "search-agent".into(),
                instance: "local".into(),
                region: None,
            },
            routing: None,
            payload,
        };

        let req = parse_request(&env).unwrap();
        assert_eq!(req.request_id, "req-123");
        assert_eq!(req.query, "how to build gateway");
        assert_eq!(req.mode, "deepsearch");
        assert_eq!(req.knowledge_scope, vec!["docs", "code"]);
        assert_eq!(req.tenant_id, "tenant-a");
        assert_eq!(req.session_id, "sess-1");
    }
}
