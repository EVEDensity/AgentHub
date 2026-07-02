//! HTTP 服务：健康检查、指标、统计、同步 diff/patch/merge。
//!
//! 端点：
//! - `GET /healthz` → "ok"
//! - `GET /metrics` → Prometheus 文本格式
//! - `GET /stats` → JSON 运行时统计
//! - `POST /diff`   → body `{"base": "...", "revised": "..."}` → [`DiffResult`]
//! - `POST /patch`  → body `{"base": "...", "patch": {"base_hash": "...", "ops": [...]}}` → [`PatchResult`]
//! - `POST /merge`  → body [`MergeRequest`] → [`MergeResult`]

use std::convert::Infallible;
use std::net::SocketAddr;
use std::sync::Arc;

use hyper::header::{HeaderValue, CONTENT_TYPE};
use hyper::service::{make_service_fn, service_fn};
use hyper::{Body, Method, Request, Response, Server, StatusCode};
use serde::{Deserialize, Serialize};
use tracing::info;

use crate::core::PatchMergeCore;
use crate::types::{MergeRequest, Patch};

pub struct PatchMergeHttpServer {
    core: Arc<PatchMergeCore>,
    addr: SocketAddr,
}

impl PatchMergeHttpServer {
    pub fn new(core: Arc<PatchMergeCore>, addr: SocketAddr) -> Self {
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
        info!(addr = %self.addr, "patch-merge-core HTTP server listening");
        Server::bind(&self.addr).serve(make_svc).await
    }
}

#[derive(Debug, Deserialize)]
struct DiffBody {
    base: String,
    revised: String,
}

#[derive(Debug, Deserialize)]
struct PatchBody {
    base: String,
    patch: Patch,
}

async fn handle(
    core: Arc<PatchMergeCore>,
    req: Request<Body>,
) -> Result<Response<Body>, Infallible> {
    let (parts, body) = req.into_parts();
    let method = &parts.method;
    let path = parts.uri.path();

    let resp = match (method, path) {
        (&Method::GET, "/healthz") => text_resp(StatusCode::OK, "ok", "text/plain"),
        (&Method::GET, "/metrics") => metrics_resp(&core).await,
        (&Method::GET, "/stats") => json_resp(StatusCode::OK, &core.stats().await),
        (&Method::POST, "/diff") => {
            let body_bytes = match hyper::body::to_bytes(body).await {
                Ok(b) => b,
                Err(_) => return Ok(text_resp(StatusCode::BAD_REQUEST, "bad body", "text/plain")),
            };
            let parsed: DiffBody = match serde_json::from_slice(&body_bytes) {
                Ok(v) => v,
                Err(e) => {
                    return Ok(json_resp(
                        StatusCode::BAD_REQUEST,
                        &serde_json::json!({"error": e.to_string()}),
                    ))
                }
            };
            match core.diff(&parsed.base, &parsed.revised).await {
                Ok(r) => json_resp(StatusCode::OK, &r),
                Err(e) => json_resp(
                    StatusCode::PAYLOAD_TOO_LARGE,
                    &serde_json::json!({"error": e.to_string()}),
                ),
            }
        }
        (&Method::POST, "/patch") => {
            let body_bytes = match hyper::body::to_bytes(body).await {
                Ok(b) => b,
                Err(_) => return Ok(text_resp(StatusCode::BAD_REQUEST, "bad body", "text/plain")),
            };
            let parsed: PatchBody = match serde_json::from_slice(&body_bytes) {
                Ok(v) => v,
                Err(e) => {
                    return Ok(json_resp(
                        StatusCode::BAD_REQUEST,
                        &serde_json::json!({"error": e.to_string()}),
                    ))
                }
            };
            match core.apply_patch(&parsed.base, &parsed.patch).await {
                Ok(r) => json_resp(StatusCode::OK, &r),
                Err(e) => json_resp(
                    StatusCode::PAYLOAD_TOO_LARGE,
                    &serde_json::json!({"error": e.to_string()}),
                ),
            }
        }
        (&Method::POST, "/merge") => {
            let body_bytes = match hyper::body::to_bytes(body).await {
                Ok(b) => b,
                Err(_) => return Ok(text_resp(StatusCode::BAD_REQUEST, "bad body", "text/plain")),
            };
            let req: MergeRequest = match serde_json::from_slice(&body_bytes) {
                Ok(v) => v,
                Err(e) => {
                    return Ok(json_resp(
                        StatusCode::BAD_REQUEST,
                        &serde_json::json!({"error": e.to_string()}),
                    ))
                }
            };
            match core.merge(&req).await {
                Ok(r) => json_resp(StatusCode::OK, &r),
                Err(e) => json_resp(
                    StatusCode::PAYLOAD_TOO_LARGE,
                    &serde_json::json!({"error": e.to_string()}),
                ),
            }
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
async fn metrics_resp(core: &Arc<PatchMergeCore>) -> Response<Body> {
    let s = core.stats().await;
    let mut out = String::with_capacity(1024);

    out.push_str("# HELP patch_merge_core_diffs_total Total diff operations.\n");
    out.push_str("# TYPE patch_merge_core_diffs_total counter\n");
    out.push_str(&format!("patch_merge_core_diffs_total {}\n", s.diffs_total));

    out.push_str("# HELP patch_merge_core_patches_total Total patch apply operations.\n");
    out.push_str("# TYPE patch_merge_core_patches_total counter\n");
    out.push_str(&format!(
        "patch_merge_core_patches_total {}\n",
        s.patches_total
    ));

    out.push_str("# HELP patch_merge_core_patches_failed_total Total failed patch applies.\n");
    out.push_str("# TYPE patch_merge_core_patches_failed_total counter\n");
    out.push_str(&format!(
        "patch_merge_core_patches_failed_total {}\n",
        s.patches_failed
    ));

    out.push_str("# HELP patch_merge_core_merges_total Total merge operations.\n");
    out.push_str("# TYPE patch_merge_core_merges_total counter\n");
    out.push_str(&format!(
        "patch_merge_core_merges_total {}\n",
        s.merges_total
    ));

    out.push_str(
        "# HELP patch_merge_core_merges_with_conflicts_total Merges that produced conflicts.\n",
    );
    out.push_str("# TYPE patch_merge_core_merges_with_conflicts_total counter\n");
    out.push_str(&format!(
        "patch_merge_core_merges_with_conflicts_total {}\n",
        s.merges_with_conflicts
    ));

    out.push_str("# HELP patch_merge_core_conflicts_total Total conflict regions across all merges.\n");
    out.push_str("# TYPE patch_merge_core_conflicts_total counter\n");
    out.push_str(&format!(
        "patch_merge_core_conflicts_total {}\n",
        s.conflicts_total
    ));

    out.push_str("# HELP patch_merge_core_avg_diff_latency_ms Average diff latency in ms.\n");
    out.push_str("# TYPE patch_merge_core_avg_diff_latency_ms gauge\n");
    out.push_str(&format!(
        "patch_merge_core_avg_diff_latency_ms {}\n",
        s.avg_diff_latency_ms
    ));

    out.push_str("# HELP patch_merge_core_avg_patch_latency_ms Average patch apply latency in ms.\n");
    out.push_str("# TYPE patch_merge_core_avg_patch_latency_ms gauge\n");
    out.push_str(&format!(
        "patch_merge_core_avg_patch_latency_ms {}\n",
        s.avg_patch_latency_ms
    ));

    out.push_str("# HELP patch_merge_core_avg_merge_latency_ms Average merge latency in ms.\n");
    out.push_str("# TYPE patch_merge_core_avg_merge_latency_ms gauge\n");
    out.push_str(&format!(
        "patch_merge_core_avg_merge_latency_ms {}\n",
        s.avg_merge_latency_ms
    ));

    text_resp(StatusCode::OK, &out, "text/plain; version=0.0.4")
}
