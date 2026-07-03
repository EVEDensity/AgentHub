//! HTTP 服务：健康检查、指标、统计、同步检索（debug）、权重读写。
//!
//! 端点：
//! - `GET /healthz` → "ok"
//! - `GET /metrics` → Prometheus 文本格式
//! - `GET /stats` → JSON 运行时统计
//! - `GET /health/details` → JSON 下游依赖健康状态
//! - `POST /retrieve` → 同步检索（debug 用，body = RetrievalRequest）
//! - `GET /weights` → 当前融合权重（BM25 / dense / rerank / freshness）
//! - `POST /weights` → 更新融合权重（自动归一化到总和 1.0）

use std::convert::Infallible;
use std::net::SocketAddr;
use std::sync::Arc;

use hyper::header::{HeaderValue, CONTENT_TYPE};
use hyper::service::{make_service_fn, service_fn};
use hyper::{Body, Method, Request, Response, Server, StatusCode};
use tracing::info;

use crate::core::RetrievalCore;
use crate::types::RetrievalRequest;

pub struct RetrievalHttpServer {
    core: Arc<RetrievalCore>,
    addr: SocketAddr,
}

impl RetrievalHttpServer {
    pub fn new(core: Arc<RetrievalCore>, addr: SocketAddr) -> Self {
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
        info!(addr = %self.addr, "retrieval-core HTTP server listening");
        Server::bind(&self.addr).serve(make_svc).await
    }
}

async fn handle(core: Arc<RetrievalCore>, req: Request<Body>) -> Result<Response<Body>, Infallible> {
    let (parts, body) = req.into_parts();
    let method = &parts.method;
    let path = parts.uri.path();

    let resp = match (method, path) {
        (&Method::GET, "/healthz") => text_resp(StatusCode::OK, "ok", "text/plain"),
        (&Method::GET, "/metrics") => metrics_resp(&core).await,
        (&Method::GET, "/stats") => json_resp(StatusCode::OK, &core.stats().await),
        (&Method::GET, "/health/details") => {
            let h = core.health().await;
            json_resp(StatusCode::OK, &h)
        }
        (&Method::POST, "/retrieve") => {
            let body_bytes = match hyper::body::to_bytes(body).await {
                Ok(b) => b,
                Err(_) => return Ok(text_resp(StatusCode::BAD_REQUEST, "bad body", "text/plain")),
            };
            let req: RetrievalRequest = match serde_json::from_slice(&body_bytes) {
                Ok(r) => r,
                Err(e) => {
                    return Ok(json_resp(
                        StatusCode::BAD_REQUEST,
                        &serde_json::json!({"error": e.to_string()}),
                    ))
                }
            };
            let result = core.retrieve(&req).await;
            json_resp(StatusCode::OK, &result)
        }
        (&Method::GET, "/weights") => {
            let w = core.weights();
            json_resp(StatusCode::OK, &w.to_json())
        }
        (&Method::POST, "/weights") => {
            let body_bytes = match hyper::body::to_bytes(body).await {
                Ok(b) => b,
                Err(_) => return Ok(text_resp(StatusCode::BAD_REQUEST, "bad body", "text/plain")),
            };
            #[derive(serde::Deserialize)]
            struct WeightsBody {
                bm25: f32,
                dense: f32,
                rerank: f32,
                freshness: f32,
            }
            let wb: WeightsBody = match serde_json::from_slice(&body_bytes) {
                Ok(w) => w,
                Err(e) => {
                    return Ok(json_resp(
                        StatusCode::BAD_REQUEST,
                        &serde_json::json!({"error": e.to_string()}),
                    ))
                }
            };
            let w = core.weights();
            w.set(wb.bm25, wb.dense, wb.rerank, wb.freshness);
            json_resp(StatusCode::OK, &w.to_json())
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

async fn metrics_resp(core: &Arc<RetrievalCore>) -> Response<Body> {
    let s = core.stats().await;
    let mut out = String::with_capacity(1024);

    out.push_str("# HELP retrieval_core_queries_total Total retrieval queries processed.\n");
    out.push_str("# TYPE retrieval_core_queries_total counter\n");
    out.push_str(&format!("retrieval_core_queries_total {}\n", s.queries_total));

    out.push_str("# HELP retrieval_core_queries_succeeded_total Successfully completed queries.\n");
    out.push_str("# TYPE retrieval_core_queries_succeeded_total counter\n");
    out.push_str(&format!(
        "retrieval_core_queries_succeeded_total {}\n",
        s.queries_succeeded
    ));

    out.push_str("# HELP retrieval_core_queries_failed_total Failed queries.\n");
    out.push_str("# TYPE retrieval_core_queries_failed_total counter\n");
    out.push_str(&format!(
        "retrieval_core_queries_failed_total {}\n",
        s.queries_failed
    ));

    out.push_str("# HELP retrieval_core_degraded_total Degradation events by type.\n");
    out.push_str("# TYPE retrieval_core_degraded_total counter\n");
    out.push_str(&format!(
        "retrieval_core_degraded_total{{type=\"dense_only\"}} {}\n",
        s.degraded_dense_only
    ));
    out.push_str(&format!(
        "retrieval_core_degraded_total{{type=\"bm25_only\"}} {}\n",
        s.degraded_bm25_only
    ));
    out.push_str(&format!(
        "retrieval_core_degraded_total{{type=\"fusion_score_only\"}} {}\n",
        s.degraded_fusion_score_only
    ));

    out.push_str("# HELP retrieval_core_avg_latency_ms Average query latency in ms.\n");
    out.push_str("# TYPE retrieval_core_avg_latency_ms gauge\n");
    out.push_str(&format!(
        "retrieval_core_avg_latency_ms {}\n",
        s.avg_latency_ms
    ));

    out.push_str("# HELP retrieval_core_qdrant_hits_total Total Qdrant hits across all queries.\n");
    out.push_str("# TYPE retrieval_core_qdrant_hits_total counter\n");
    out.push_str(&format!(
        "retrieval_core_qdrant_hits_total {}\n",
        s.total_qdrant_hits
    ));

    out.push_str("# HELP retrieval_core_opensearch_hits_total Total OpenSearch hits across all queries.\n");
    out.push_str("# TYPE retrieval_core_opensearch_hits_total counter\n");
    out.push_str(&format!(
        "retrieval_core_opensearch_hits_total {}\n",
        s.total_opensearch_hits
    ));

    text_resp(StatusCode::OK, &out, "text/plain; version=0.0.4")
}
