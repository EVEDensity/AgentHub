//! HTTP 服务：健康检查、指标、统计、同步 compact / prune / checkpoint。
//!
//! 端点：
//! - `GET  /healthz`    → "ok"
//! - `GET  /metrics`    → Prometheus 文本格式
//! - `GET  /stats`      → JSON 运行时统计
//! - `POST /compact`    → body `{"messages": [...], "config": {...}?}` → [`CompactResult`]
//! - `POST /prune`      → body `{"messages": [...]}`                     → [`PruneResult`]
//! - `POST /checkpoint` → body `{"messages": [...]}`                     → [`Checkpoint`]
//!
//! 配置覆盖：`POST /compact` 可选 `config` 字段，缺失字段用默认值
//! （[`CompactionConfig`] 已实现 `Deserialize` + `#[serde(default)]`）。

use std::convert::Infallible;
use std::net::SocketAddr;
use std::sync::Arc;

use hyper::header::{HeaderValue, CONTENT_TYPE};
use hyper::service::{make_service_fn, service_fn};
use hyper::{Body, Method, Request, Response, Server, StatusCode};
use serde::{Deserialize, Serialize};
use tracing::info;

use crate::core::MemorySegmentCore;
use crate::types::{Checkpoint, CompactionConfig, Message, PruneResult};

pub struct MemorySegmentHttpServer {
    core: Arc<MemorySegmentCore>,
    addr: SocketAddr,
}

impl MemorySegmentHttpServer {
    pub fn new(core: Arc<MemorySegmentCore>, addr: SocketAddr) -> Self {
        Self { core, addr }
    }

    pub async fn serve(self) -> Result<(), hyper::Error> {
        let core = Arc::clone(&self.core);
        let make_svc = make_service_fn(move |_conn| {
            let core = Arc::clone(&core);
            async move {
                Ok::<_, Infallible>(service_fn(move |req| {
                    let core = Arc::clone(&core);
                    async move { handle(core, req).await }
                }))
            }
        });
        info!(addr = %self.addr, "memory-segment-core HTTP server listening");
        Server::bind(&self.addr).serve(make_svc).await
    }
}

/// `/compact` 请求体：messages 必填，config 可选（缺失字段用默认）。
#[derive(Debug, Deserialize)]
struct CompactBody {
    messages: Vec<Message>,
    #[serde(default)]
    config: Option<CompactionConfig>,
}

/// `/prune` 与 `/checkpoint` 请求体：仅需 messages。
#[derive(Debug, Deserialize)]
struct MessagesBody {
    messages: Vec<Message>,
}

async fn handle(
    core: Arc<MemorySegmentCore>,
    req: Request<Body>,
) -> Result<Response<Body>, Infallible> {
    let (parts, body) = req.into_parts();
    let method = &parts.method;
    let path = parts.uri.path();

    let resp = match (method, path) {
        (&Method::GET, "/healthz") => text_resp(StatusCode::OK, "ok", "text/plain"),
        (&Method::GET, "/metrics") => metrics_resp(&core).await,
        (&Method::GET, "/stats") => json_resp(StatusCode::OK, &core.stats().await),
        (&Method::POST, "/compact") => {
            let body_bytes = match hyper::body::to_bytes(body).await {
                Ok(b) => b,
                Err(_) => return Ok(text_resp(StatusCode::BAD_REQUEST, "bad body", "text/plain")),
            };
            let parsed: CompactBody = match serde_json::from_slice(&body_bytes) {
                Ok(v) => v,
                Err(e) => {
                    return Ok(json_resp(
                        StatusCode::BAD_REQUEST,
                        &serde_json::json!({"error": e.to_string()}),
                    ))
                }
            };
            let result = match parsed.config {
                Some(cfg) => core.compact_with(&parsed.messages, &cfg).await,
                None => core.compact(&parsed.messages).await,
            };
            json_resp(StatusCode::OK, &result)
        }
        (&Method::POST, "/prune") => {
            let body_bytes = match hyper::body::to_bytes(body).await {
                Ok(b) => b,
                Err(_) => return Ok(text_resp(StatusCode::BAD_REQUEST, "bad body", "text/plain")),
            };
            let parsed: MessagesBody = match serde_json::from_slice(&body_bytes) {
                Ok(v) => v,
                Err(e) => {
                    return Ok(json_resp(
                        StatusCode::BAD_REQUEST,
                        &serde_json::json!({"error": e.to_string()}),
                    ))
                }
            };
            let result: PruneResult = core.prune(&parsed.messages).await;
            json_resp(StatusCode::OK, &result)
        }
        (&Method::POST, "/checkpoint") => {
            let body_bytes = match hyper::body::to_bytes(body).await {
                Ok(b) => b,
                Err(_) => return Ok(text_resp(StatusCode::BAD_REQUEST, "bad body", "text/plain")),
            };
            let parsed: MessagesBody = match serde_json::from_slice(&body_bytes) {
                Ok(v) => v,
                Err(e) => {
                    return Ok(json_resp(
                        StatusCode::BAD_REQUEST,
                        &serde_json::json!({"error": e.to_string()}),
                    ))
                }
            };
            let result: Checkpoint = core.checkpoint(&parsed.messages).await;
            json_resp(StatusCode::OK, &result)
        }
        _ => text_resp(StatusCode::NOT_FOUND, "not found", "text/plain"),
    };
    Ok(resp)
}

fn text_resp(status: StatusCode, body: &str, ct: &str) -> Response<Body> {
    Response::builder()
        .status(status)
        .header(CONTENT_TYPE, HeaderValue::from_str(ct).unwrap())
        .body(Body::from(body.to_string()))
        .unwrap()
}

fn json_resp<T: Serialize>(status: StatusCode, val: &T) -> Response<Body> {
    let body = serde_json::to_vec(val).unwrap_or_else(|_| b"{}".to_vec());
    Response::builder()
        .status(status)
        .header(CONTENT_TYPE, HeaderValue::from_static("application/json"))
        .body(Body::from(body))
        .unwrap()
}

/// 手写 Prometheus 文本格式，避免引入 prometheus crate。
async fn metrics_resp(core: &Arc<MemorySegmentCore>) -> Response<Body> {
    let s = core.stats().await;
    let mut out = String::with_capacity(1024);

    out.push_str("# HELP memory_segment_core_compacts_total Total compact operations.\n");
    out.push_str("# TYPE memory_segment_core_compacts_total counter\n");
    out.push_str(&format!(
        "memory_segment_core_compacts_total {}\n",
        s.compacts_total
    ));

    out.push_str(
        "# HELP memory_segment_core_compacts_triggered_total Compacts that actually triggered compression.\n",
    );
    out.push_str("# TYPE memory_segment_core_compacts_triggered_total counter\n");
    out.push_str(&format!(
        "memory_segment_core_compacts_triggered_total {}\n",
        s.compacts_triggered
    ));

    out.push_str("# HELP memory_segment_core_prunes_total Total prune operations.\n");
    out.push_str("# TYPE memory_segment_core_prunes_total counter\n");
    out.push_str(&format!(
        "memory_segment_core_prunes_total {}\n",
        s.prunes_total
    ));

    out.push_str(
        "# HELP memory_segment_core_checkpoints_total Total checkpoint operations.\n",
    );
    out.push_str("# TYPE memory_segment_core_checkpoints_total counter\n");
    out.push_str(&format!(
        "memory_segment_core_checkpoints_total {}\n",
        s.checkpoints_total
    ));

    out.push_str(
        "# HELP memory_segment_core_messages_compacted_total Total messages folded into summaries.\n",
    );
    out.push_str("# TYPE memory_segment_core_messages_compacted_total counter\n");
    out.push_str(&format!(
        "memory_segment_core_messages_compacted_total {}\n",
        s.messages_compacted_total
    ));

    out.push_str(
        "# HELP memory_segment_core_tokens_reduced_total Total tokens saved across all operations.\n",
    );
    out.push_str("# TYPE memory_segment_core_tokens_reduced_total counter\n");
    out.push_str(&format!(
        "memory_segment_core_tokens_reduced_total {}\n",
        s.tokens_reduced_total
    ));

    out.push_str(
        "# HELP memory_segment_core_avg_compact_latency_ms Average compact latency in ms.\n",
    );
    out.push_str("# TYPE memory_segment_core_avg_compact_latency_ms gauge\n");
    out.push_str(&format!(
        "memory_segment_core_avg_compact_latency_ms {}\n",
        s.avg_compact_latency_ms
    ));

    out.push_str(
        "# HELP memory_segment_core_avg_prune_latency_ms Average prune latency in ms.\n",
    );
    out.push_str("# TYPE memory_segment_core_avg_prune_latency_ms gauge\n");
    out.push_str(&format!(
        "memory_segment_core_avg_prune_latency_ms {}\n",
        s.avg_prune_latency_ms
    ));

    out.push_str(
        "# HELP memory_segment_core_avg_checkpoint_latency_ms Average checkpoint latency in ms.\n",
    );
    out.push_str("# TYPE memory_segment_core_avg_checkpoint_latency_ms gauge\n");
    out.push_str(&format!(
        "memory_segment_core_avg_checkpoint_latency_ms {}\n",
        s.avg_checkpoint_latency_ms
    ));

    text_resp(StatusCode::OK, &out, "text/plain; version=0.0.4")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn core() -> Arc<MemorySegmentCore> {
        MemorySegmentCore::new(CompactionConfig {
            compact_trigger_messages: 3,
            keep_recent: 1,
            ..Default::default()
        })
    }

    fn msgs(n: usize) -> Vec<Message> {
        use crate::types::MessageRole;
        (1..=n)
            .map(|i| Message::new(i as u64, MessageRole::User, format!("msg {i}")))
            .collect()
    }

    #[tokio::test]
    async fn healthz_returns_ok() {
        let c = core();
        let req = Request::builder()
            .method(Method::GET)
            .uri("/healthz")
            .body(Body::empty())
            .unwrap();
        let resp = handle(c, req).await.unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        let bytes = hyper::body::to_bytes(resp.into_body()).await.unwrap();
        assert_eq!(&bytes[..], b"ok");
    }

    #[tokio::test]
    async fn stats_returns_json() {
        let c = core();
        let req = Request::builder()
            .method(Method::GET)
            .uri("/stats")
            .body(Body::empty())
            .unwrap();
        let resp = handle(c, req).await.unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        let bytes = hyper::body::to_bytes(resp.into_body()).await.unwrap();
        let v: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(v["compacts_total"], 0);
    }

    #[tokio::test]
    async fn compact_endpoint_returns_result() {
        let c = core();
        // messages(5) ≥ trigger(3) → 触发压缩。
        let body = serde_json::json!({ "messages": msgs(5) });
        let req = Request::builder()
            .method(Method::POST)
            .uri("/compact")
            .body(Body::from(serde_json::to_vec(&body).unwrap()))
            .unwrap();
        let resp = handle(c, req).await.unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        let bytes = hyper::body::to_bytes(resp.into_body()).await.unwrap();
        let v: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(v["compacted"], true);
        assert!(v["summary"].is_object());
    }

    #[tokio::test]
    async fn compact_with_config_override() {
        let c = core();
        // 用一个很大的 trigger 避免触发压缩。
        let body = serde_json::json!({
            "messages": msgs(5),
            "config": {"compact_trigger_messages": 100, "keep_recent": 2},
        });
        let req = Request::builder()
            .method(Method::POST)
            .uri("/compact")
            .body(Body::from(serde_json::to_vec(&body).unwrap()))
            .unwrap();
        let resp = handle(c, req).await.unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        let bytes = hyper::body::to_bytes(resp.into_body()).await.unwrap();
        let v: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(v["compacted"], false);
    }

    #[tokio::test]
    async fn prune_endpoint_returns_result() {
        let c = core();
        let body = serde_json::json!({"messages": msgs(5)});
        let req = Request::builder()
            .method(Method::POST)
            .uri("/prune")
            .body(Body::from(serde_json::to_vec(&body).unwrap()))
            .unwrap();
        let resp = handle(c, req).await.unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        let bytes = hyper::body::to_bytes(resp.into_body()).await.unwrap();
        let v: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(v["pruned_count"], 4); // keep_recent=1 → 丢弃 4 条
        assert_eq!(v["retained"].as_array().unwrap().len(), 1);
    }

    #[tokio::test]
    async fn checkpoint_endpoint_returns_result() {
        let c = core();
        let body = serde_json::json!({"messages": msgs(3)});
        let req = Request::builder()
            .method(Method::POST)
            .uri("/checkpoint")
            .body(Body::from(serde_json::to_vec(&body).unwrap()))
            .unwrap();
        let resp = handle(c, req).await.unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        let bytes = hyper::body::to_bytes(resp.into_body()).await.unwrap();
        let v: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        assert!(v["summary_text"].is_string());
        assert_eq!(v["covered_message_count"], 3);
    }

    #[tokio::test]
    async fn compact_bad_body_returns_400() {
        let c = core();
        let req = Request::builder()
            .method(Method::POST)
            .uri("/compact")
            .body(Body::from(b"not json".to_vec()))
            .unwrap();
        let resp = handle(c, req).await.unwrap();
        assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    }

    #[tokio::test]
    async fn unknown_path_returns_404() {
        let c = core();
        let req = Request::builder()
            .method(Method::GET)
            .uri("/nope")
            .body(Body::empty())
            .unwrap();
        let resp = handle(c, req).await.unwrap();
        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    }

    #[tokio::test]
    async fn metrics_returns_prometheus_text() {
        let c = core();
        // 先打一次 compact 让统计有数据。
        c.compact(&msgs(5)).await;
        let req = Request::builder()
            .method(Method::GET)
            .uri("/metrics")
            .body(Body::empty())
            .unwrap();
        let resp = handle(c, req).await.unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        let bytes = hyper::body::to_bytes(resp.into_body()).await.unwrap();
        let text = String::from_utf8(bytes.to_vec()).unwrap();
        assert!(text.contains("memory_segment_core_compacts_total 1"));
        assert!(text.contains("# TYPE memory_segment_core_compacts_total counter"));
    }
}
