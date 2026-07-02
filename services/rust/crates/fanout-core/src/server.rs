//! HTTP 服务：健康检查、指标、统计、订阅管理、同步路由（debug）。
//!
//! 端点：
//! - `GET /healthz` → "ok"
//! - `GET /metrics` → Prometheus 文本格式
//! - `GET /stats` → JSON 运行时统计
//! - `GET /subscribers` → JSON 各频道订阅者统计
//! - `GET /channels` → JSON 各频道视角统计
//! - `GET /partitions` → JSON 当前分区数
//! - `POST /route` → 同步路由（debug 用，body = FanoutEvent）

use std::convert::Infallible;
use std::net::SocketAddr;
use std::sync::Arc;

use hyper::header::{HeaderValue, CONTENT_TYPE};
use hyper::service::{make_service_fn, service_fn};
use hyper::{Body, Method, Request, Response, Server, StatusCode};
use tracing::info;

use crate::core::FanoutCore;
use crate::types::FanoutEvent;

pub struct FanoutHttpServer {
    core: Arc<FanoutCore>,
    addr: SocketAddr,
}

impl FanoutHttpServer {
    pub fn new(core: Arc<FanoutCore>, addr: SocketAddr) -> Self {
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
        info!(addr = %self.addr, "fanout-core HTTP server listening");
        Server::bind(&self.addr).serve(make_svc).await
    }
}

async fn handle(core: Arc<FanoutCore>, req: Request<Body>) -> Result<Response<Body>, Infallible> {
    let (parts, body) = req.into_parts();
    let method = &parts.method;
    let path = parts.uri.path();

    let resp = match (method, path) {
        (&Method::GET, "/healthz") => text_resp(StatusCode::OK, "ok", "text/plain"),
        (&Method::GET, "/metrics") => metrics_resp(&core).await,
        (&Method::GET, "/stats") => json_resp(StatusCode::OK, &core.stats().await),
        (&Method::GET, "/subscribers") => json_resp(StatusCode::OK, &core.subscriber_stats().await),
        (&Method::GET, "/channels") => json_resp(StatusCode::OK, &core.channel_stats().await),
        (&Method::GET, "/partitions") => {
            json_resp(
                StatusCode::OK,
                &serde_json::json!({"partitions": core.partition_count().await}),
            )
        }
        (&Method::POST, "/route") => {
            let body_bytes = match hyper::body::to_bytes(body).await {
                Ok(b) => b,
                Err(_) => return Ok(text_resp(StatusCode::BAD_REQUEST, "bad body", "text/plain")),
            };
            let evt: FanoutEvent = match serde_json::from_slice(&body_bytes) {
                Ok(e) => e,
                Err(e) => {
                    return Ok(json_resp(
                        StatusCode::BAD_REQUEST,
                        &serde_json::json!({"error": e.to_string()}),
                    ))
                }
            };
            let receipt = core.route(evt).await;
            json_resp(StatusCode::OK, &receipt)
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

fn json_resp<T: serde::Serialize>(status: StatusCode, val: &T) -> Response<Body> {
    let body = serde_json::to_vec(val).unwrap_or_else(|_| b"{}".to_vec());
    Response::builder()
        .status(status)
        .header(CONTENT_TYPE, HeaderValue::from_static("application/json"))
        .body(Body::from(body))
        .unwrap()
}

/// 手写 Prometheus 文本格式，避免引入 prometheus crate。
async fn metrics_resp(core: &Arc<FanoutCore>) -> Response<Body> {
    let s = core.stats().await;
    let mut out = String::with_capacity(1024);

    out.push_str("# HELP fanout_core_events_total Total events routed.\n");
    out.push_str("# TYPE fanout_core_events_total counter\n");
    out.push_str(&format!("fanout_core_events_total {}\n", s.events_total));

    out.push_str("# HELP fanout_core_events_delivered_total Total events delivered to subscribers.\n");
    out.push_str("# TYPE fanout_core_events_delivered_total counter\n");
    out.push_str(&format!(
        "fanout_core_events_delivered_total {}\n",
        s.events_delivered
    ));

    out.push_str("# HELP fanout_core_events_dropped_total Total events dropped (slow subscribers).\n");
    out.push_str("# TYPE fanout_core_events_dropped_total counter\n");
    out.push_str(&format!(
        "fanout_core_events_dropped_total {}\n",
        s.events_dropped
    ));

    out.push_str("# HELP fanout_core_degraded_total Total degradation events.\n");
    out.push_str("# TYPE fanout_core_degraded_total counter\n");
    out.push_str(&format!("fanout_core_degraded_total {}\n", s.degraded_total));

    out.push_str("# HELP fanout_core_active_subscribers Active subscriber count.\n");
    out.push_str("# TYPE fanout_core_active_subscribers gauge\n");
    out.push_str(&format!(
        "fanout_core_active_subscribers {}\n",
        s.active_subscribers
    ));

    out.push_str("# HELP fanout_core_active_channels Active channel count.\n");
    out.push_str("# TYPE fanout_core_active_channels gauge\n");
    out.push_str(&format!(
        "fanout_core_active_channels {}\n",
        s.active_channels
    ));

    out.push_str("# HELP fanout_core_partition_count Current partition count.\n");
    out.push_str("# TYPE fanout_core_partition_count gauge\n");
    out.push_str(&format!(
        "fanout_core_partition_count {}\n",
        s.partition_count
    ));

    out.push_str("# HELP fanout_core_avg_latency_ms Average route latency in ms.\n");
    out.push_str("# TYPE fanout_core_avg_latency_ms gauge\n");
    out.push_str(&format!(
        "fanout_core_avg_latency_ms {}\n",
        s.avg_latency_ms
    ));

    text_resp(StatusCode::OK, &out, "text/plain; version=0.0.4")
}
