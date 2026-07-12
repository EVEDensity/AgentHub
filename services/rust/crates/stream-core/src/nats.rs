//! NATS 集成：订阅 `agenthub.session.stream.events`，把 platform-events 的
//! [`EventEnvelope`](platform_events::EventEnvelope) 转成 [`StreamChunk`] 并
//! 推入 [`StreamCore`]。
//!
//! stream-core 作为无状态 fanout 加速器：用 core NATS 订阅接收实时流（JetStream
//! publish 会同时投递给 core 订阅者）；持久化回放由 stream-delivery-service 的
//! Redis Streams 负责，职责分离。

use std::sync::Arc;

use async_nats::Client;
use futures::StreamExt;

use platform_events::EventEnvelope;

use crate::chunk::{ChunkKind, ChunkMeta, StreamChunk};
use crate::core::StreamCore;
use tokio::task::JoinHandle;

/// stream-core 订阅的 NATS subject（与 Go eventbus 常量一致）。
pub const STREAM_EVENTS_SUBJECT: &str = "agenthub.session.stream.events";

/// 与 Go 端 EventType 常量对齐。
const EVT_CHUNK: &str = "session.stream.chunk";
const EVT_FLUSH: &str = "session.stream.flush";
const EVT_COMPLETE: &str = "session.stream.complete";
const EVT_ERROR: &str = "session.stream.error";

/// NATS 适配器：持有连接。订阅 task 由 [`Self::spawn_subscription`] 启动，
/// 持有 Subscriber 的所有权；本结构只保留 client 用于停机断开。
pub struct NatsAdapter {
    client: Option<Client>,
}

impl NatsAdapter {
    /// 连接 NATS（带自动重连）。`url` 形如 `nats://nats:4222`。
    pub async fn connect(url: &str) -> Result<Self, async_nats::Error> {
        let client = async_nats::connect(url).await?;
        Ok(Self { client: Some(client) })
    }

    /// 启动订阅循环：把 stream.events 上的 envelope 转成 chunk 推入 StreamCore。
    /// 返回 task join handle，便于停机 abort。
    pub async fn spawn_subscription(
        &self,
        core: Arc<StreamCore>,
    ) -> Result<JoinHandle<()>, async_nats::Error> {
        let client = self.client.as_ref().expect("nats client missing");
        let mut sub = client.subscribe(STREAM_EVENTS_SUBJECT).await?;
        Ok(tokio::spawn(async move {
            tracing::info!(subject = STREAM_EVENTS_SUBJECT, "stream-core subscribed");
            while let Some(msg) = sub.next().await {
                let env = match serde_json::from_slice::<EventEnvelope>(&msg.payload) {
                    Ok(e) => e,
                    Err(e) => {
                        tracing::warn!(error = %e, "failed to parse envelope, skipping");
                        continue;
                    }
                };
                let chunk = match envelope_to_chunk(&env) {
                    Some(c) => c,
                    None => {
                        // 非流式事件（如 session.message.received），忽略。
                        continue;
                    }
                };
                // 推入 StreamCore；背压满时按策略降级（已计入统计）。
                if let Err(c) = core.ingest(chunk).await {
                    tracing::warn!(
                        session_id = %c.meta.session_id,
                        kind = ?c.kind,
                        "backpressure dropped chunk"
                    );
                }
            }
            tracing::info!("stream-core subscription ended");
        }))
    }

    /// 断开 NATS 连接。
    pub async fn close(mut self) {
        // drop client 触发断开；subscription task 会因连接关闭而结束。
        if let Some(c) = self.client.take() {
            drop(c);
        }
    }
}

/// 把 platform-events 的 Envelope 映射为 StreamChunk。
/// 仅处理 `session.stream.*` 事件；其余返回 None。
fn envelope_to_chunk(env: &EventEnvelope) -> Option<StreamChunk> {
    let kind = match env.event_type.as_str() {
        EVT_CHUNK => ChunkKind::Delta,
        EVT_FLUSH => ChunkKind::Flush,
        EVT_COMPLETE => ChunkKind::Complete,
        EVT_ERROR => ChunkKind::Error,
        _ => return None, // 非流式事件，stream-core 不处理。
    };
    // content 字段：chunk 事件取 payload["content"]；flush/complete 可能为空。
    let content = match env.payload.get("content") {
        Some(serde_json::Value::String(s)) => s.clone(),
        Some(v) if !v.is_null() => v.to_string(),
        _ => String::new(),
    };
    let message_id = env.message_id.clone().unwrap_or_default();
    let meta = ChunkMeta {
        tenant_id: env.tenant_id.clone(),
        session_id: env.session_id.clone(),
        message_id,
        trace_id: env.trace_id.clone(),
        sequence: 0, // 由 StreamCore 分配。
        produced_at: env.occurred_at,
    };
    Some(StreamChunk {
        kind,
        meta,
        content,
        extra: env.payload.clone().into_iter().collect(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Utc;
    use platform_events::{EventEnvelope, Producer};
    use serde_json::json;

    fn env(evt: &str, content: Option<&str>) -> EventEnvelope {
        let mut payload = std::collections::BTreeMap::new();
        payload.insert("stream_id".to_string(), json!("sid-1"));
        if let Some(c) = content {
            payload.insert("content".to_string(), json!(c));
        }
        EventEnvelope {
            event_id: "e1".into(),
            event_type: evt.to_string(),
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
            routing: None,
            payload,
        }
    }

    #[test]
    fn chunk_event_maps_to_delta() {
        let e = env(EVT_CHUNK, Some("hello"));
        let c = envelope_to_chunk(&e).unwrap();
        assert_eq!(c.kind, ChunkKind::Delta);
        assert_eq!(c.content, "hello");
    }

    #[test]
    fn flush_event_maps_to_flush() {
        let e = env(EVT_FLUSH, None);
        let c = envelope_to_chunk(&e).unwrap();
        assert_eq!(c.kind, ChunkKind::Flush);
        assert!(c.content.is_empty());
    }

    #[test]
    fn complete_event_maps_to_complete() {
        let e = env(EVT_COMPLETE, None);
        let c = envelope_to_chunk(&e).unwrap();
        assert_eq!(c.kind, ChunkKind::Complete);
    }

    #[test]
    fn non_stream_event_returns_none() {
        let e = env("session.message.received", Some("hi"));
        assert!(envelope_to_chunk(&e).is_none());
    }
}
