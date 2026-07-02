//! HTTP 服务：暴露健康检查、统计、SSE fanout 与 lag 报告。
//!
//! 端点：
//! - `GET /healthz` → "ok"
//! - `GET /metrics` → Prometheus 文本格式指标
//! - `GET /stats` → JSON 全量统计
//! - `GET /consumers` → JSON 消费者统计
//! - `GET /streams/lag-reports` → JSON 最近 lag 事件
//! - `GET /streams/sse?session_id=...&consumer_id=...` → SSE 流（fanout 给客户端）
//! - `POST /consumers/unsubscribe?session_id=...&consumer_id=...` → 退订
//!
//! SSE 流是 stream-core 对外的主要出口：stream-delivery-service 或网关的
//! WebSocket hub 可订阅 stream-core 的 SSE，把合并后的批次转发给终端客户端。

use std::convert::Infallible;
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use futures::stream::{self, StreamExt};
use futures::Stream;
use hyper::body::Bytes;
use hyper::header::{HeaderValue, CONTENT_TYPE};
use hyper::service::{make_service_fn, service_fn};
use hyper::{Body, Method, Request, Response, Server, StatusCode};

use crate::consumer::ConsumerHandle;
use crate::core::StreamCore;

/// SSE 单条数据的最大等待时间；超时发心跳帧保活。
const SSE_PULL_TIMEOUT: Duration = Duration::from_millis(800);
/// SSE 心跳帧。
const SSE_HEARTBEAT: &str = ":heartbeat\n\n";

pub struct StreamHttpServer {
    core: Arc<StreamCore>,
    addr: SocketAddr,
}

impl StreamHttpServer {
    pub fn new(core: Arc<StreamCore>, addr: SocketAddr) -> Self {
        Self { core, addr }
    }

    /// 启动 HTTP 服务，阻塞至服务结束。
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
        Server::bind(&self.addr).serve(make_svc).await
    }
}

async fn handle(core: Arc<StreamCore>, req: Request<Body>) -> Result<Response<Body>, Infallible> {
    let (parts, _body) = req.into_parts();
    let method = &parts.method;
    let path = parts.uri.path();
    let query = parts.uri.query().unwrap_or("");

    let resp = match (method, path) {
        (&Method::GET, "/healthz") => text_resp(StatusCode::OK, "ok", "text/plain"),
        (&Method::GET, "/metrics") => metrics_resp(&core).await,
        (&Method::GET, "/stats") => json_resp(StatusCode::OK, &core.stats().await),
        (&Method::GET, "/consumers") => json_resp(StatusCode::OK, &core.consumer_stats().await),
        (&Method::GET, "/streams/lag-reports") => {
            json_resp(StatusCode::OK, &core.lag_reports().await)
        }
        (&Method::GET, "/streams/sse") => sse_resp(core, query).await,
        (&Method::POST, "/consumers/unsubscribe") => unsubscribe_resp(core, query).await,
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

fn json_resp<T: serde::Serialize>(status: StatusCode, val: &T) -> Response<Body> {
    let body = serde_json::to_vec(val).unwrap_or_else(|_| b"{}".to_vec());
    Response::builder()
        .status(status)
        .header(CONTENT_TYPE, HeaderValue::from_static("application/json"))
        .body(Body::from(body))
        .unwrap()
}

/// 手写 Prometheus 文本格式，避免引入 prometheus crate。
async fn metrics_resp(core: &Arc<StreamCore>) -> Response<Body> {
    let s = core.stats().await;
    let mut out = String::with_capacity(1024);
    out.push_str("# HELP stream_core_ingested_total Total chunks ingested from upstream.\n");
    out.push_str("# TYPE stream_core_ingested_total counter\n");
    out.push_str(&format!("stream_core_ingested_total {}\n", s.ingested));

    out.push_str("# HELP stream_core_batches_emitted_total Total merged batches emitted.\n");
    out.push_str("# TYPE stream_core_batches_emitted_total counter\n");
    out.push_str(&format!(
        "stream_core_batches_emitted_total {}\n",
        s.batches_emitted
    ));

    out.push_str("# HELP stream_core_degraded_total Total slow-consumer degradation events.\n");
    out.push_str("# TYPE stream_core_degraded_total counter\n");
    out.push_str(&format!("stream_core_degraded_total {}\n", s.degraded_total));

    out.push_str("# HELP stream_core_bp_dropped_total Total chunks dropped by backpressure.\n");
    out.push_str("# TYPE stream_core_bp_dropped_total counter\n");
    out.push_str(&format!(
        "stream_core_bp_dropped_total {}\n",
        s.backpressure.dropped_oldest + s.backpressure.dropped_newest
    ));

    out.push_str("# HELP stream_core_bp_coalesced_total Total chunks coalesced by backpressure.\n");
    out.push_str("# TYPE stream_core_bp_coalesced_total counter\n");
    out.push_str(&format!(
        "stream_core_bp_coalesced_total {}\n",
        s.backpressure.coalesced
    ));

    out.push_str("# HELP stream_core_merger_buffered_chunks Currently buffered chunks awaiting flush.\n");
    out.push_str("# TYPE stream_core_merger_buffered_chunks gauge\n");
    out.push_str(&format!(
        "stream_core_merger_buffered_chunks {}\n",
        s.merger_buffered_chunks
    ));

    out.push_str("# HELP stream_core_active_sessions Sessions with active subscribers.\n");
    out.push_str("# TYPE stream_core_active_sessions gauge\n");
    out.push_str(&format!("stream_core_active_sessions {}\n", s.active_sessions));

    out.push_str("# HELP stream_core_active_consumers Active consumer subscriptions.\n");
    out.push_str("# TYPE stream_core_active_consumers gauge\n");
    out.push_str(&format!(
        "stream_core_active_consumers {}\n",
        s.active_consumers
    ));

    text_resp(StatusCode::OK, &out, "text/plain; version=0.0.4")
}

/// 解析 query string 中的 key。
fn query_get<'a>(query: &'a str, key: &str) -> Option<&'a str> {
    for pair in query.split('&') {
        let mut it = pair.splitn(2, '=');
        if it.next()? == key {
            return Some(it.next().unwrap_or(""));
        }
    }
    None
}

async fn sse_resp(core: Arc<StreamCore>, query: &str) -> Response<Body> {
    let session_id = match query_get(query, "session_id") {
        Some(s) if !s.is_empty() => s.to_string(),
        _ => {
            return json_resp(
                StatusCode::BAD_REQUEST,
                &serde_json::json!({"error": "session_id is required"}),
            )
        }
    };
    let consumer_id = query_get(query, "consumer_id").map(|s| s.to_string());

    let handle = core.subscribe(&session_id, consumer_id.as_deref()).await;
    let cid = handle.id().to_string();
    tracing::info!(session_id = %session_id, consumer_id = %cid, "sse consumer subscribed");

    let sse = build_sse_stream(handle, Arc::clone(&core), session_id.clone(), cid.clone());

    Response::builder()
        .status(StatusCode::OK)
        .header(CONTENT_TYPE, HeaderValue::from_static("text/event-stream"))
        .header("Cache-Control", HeaderValue::from_static("no-cache"))
        .header("Connection", HeaderValue::from_static("keep-alive"))
        .body(Body::wrap_stream(sse))
        .unwrap()
}

/// 构造 SSE 流：从消费者句柄取批次 → 序列化为 SSE 帧 → terminal 时结束并退订。
fn build_sse_stream(
    handle: ConsumerHandle,
    core: Arc<StreamCore>,
    session_id: String,
    consumer_id: String,
) -> impl Stream<Item = Result<Bytes, std::io::Error>> + Send {
    let core_for_loop = Arc::clone(&core);
    let sid_for_loop = session_id.clone();
    let cid_for_loop = consumer_id.clone();

    // 主循环：unfold 产出帧，None 时结束。
    let main = stream::unfold(handle, move |handle| {
        let core = Arc::clone(&core_for_loop);
        let sid = sid_for_loop.clone();
        let cid = cid_for_loop.clone();
        async move {
            let batch = handle.next_timeout(SSE_PULL_TIMEOUT).await;
            // 周期性 note_drain，避免被 reaper 误回收。
            core.registry().note_drain(&sid, &cid).await;
            match batch {
                Some(b) => {
                    let terminal = b.terminal;
                    let payload = serde_json::to_string(&b).unwrap_or_else(|_| "{}".into());
                    let frame = format!("event: batch\ndata: {}\n\n", payload);
                    if terminal {
                        None // 结束流（terminal 批次已发出）
                    } else {
                        Some((Ok::<_, std::io::Error>(Bytes::from(frame)), handle))
                    }
                }
                None => Some((Ok(Bytes::from(SSE_HEARTBEAT)), handle)),
            }
        }
    });

    // 流结束后退订（chain 一个 once 流，先发结束帧再清理）。
    main.chain(stream::once(async move {
        core.unsubscribe(&session_id, &consumer_id).await;
        tracing::info!(session_id = %session_id, consumer_id = %consumer_id, "sse consumer unsubscribed (stream end)");
        Ok::<_, std::io::Error>(Bytes::from("event: end\ndata: {\"closed\":true}\n\n"))
    }))
}

async fn unsubscribe_resp(core: Arc<StreamCore>, query: &str) -> Response<Body> {
    let session_id = match query_get(query, "session_id") {
        Some(s) if !s.is_empty() => s,
        _ => {
            return json_resp(
                StatusCode::BAD_REQUEST,
                &serde_json::json!({"error": "session_id is required"}),
            )
        }
    };
    let consumer_id = match query_get(query, "consumer_id") {
        Some(s) if !s.is_empty() => s,
        _ => {
            return json_resp(
                StatusCode::BAD_REQUEST,
                &serde_json::json!({"error": "consumer_id is required"}),
            )
        }
    };
    core.unsubscribe(session_id, consumer_id).await;
    json_resp(StatusCode::OK, &serde_json::json!({"unsubscribed": true}))
}
