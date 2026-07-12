//! patch-merge-core-service 主程序。
//!
//! 启动流程：
//! 1. 初始化日志（tracing + env-filter）。
//! 2. 从环境变量构建 [`DiffConfig`]，组装 [`PatchMergeCore`]。
//! 3. **先 spawn HTTP 服务**（/healthz, /metrics, /stats, POST /diff /patch /merge），
//!    确保 K8s liveness/readiness 探针立即可用——即使 NATS 还在重试，/healthz 已可响应。
//! 4. 在主 future 里连接 NATS 并订阅 `agenthub.patch.merge.requested`，把 envelope
//!    解析为 [`MergeRequest`] 后调 `core.merge()`，结果发布到 `agenthub.patch.audit`。
//!    主 future 由 block_on 驱动不要求 Send，故 NATS 重试（含非 Send 错误类型）不阻塞
//!    独立的 HTTP task。
//! 5. 周期性统计日志。
//! 6. 监听 Ctrl+C 优雅停机。
//!
//! 环境变量：
//! - `NATS_URL`（默认 `nats://127.0.0.1:4222`）
//! - `PATCH_MERGE_CORE_ADDR`（默认 `0.0.0.0:8104`）
//! - `RUST_LOG`（默认 `info,patch_merge_core=info`）
//! - `PMC_MAX_TEXT_BYTES`（默认 8388608 = 8 MiB）
//! - `PMC_MAX_OPS`（默认 200000）

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use patch_merge_core::core::PatchMergeCore;
use patch_merge_core::nats::NatsAdapter;
use patch_merge_core::server::PatchMergeHttpServer;
use patch_merge_core::types::DiffConfig;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    init_tracing();

    let addr: SocketAddr = getenv_or("PATCH_MERGE_CORE_ADDR", "0.0.0.0:8104")
        .parse()
        .expect("invalid PATCH_MERGE_CORE_ADDR");
    let nats_url = getenv_or("NATS_URL", "nats://127.0.0.1:4222");

    tracing::info!(
        version = patch_merge_core::VERSION,
        addr = %addr,
        nats_url = %nats_url,
        "starting patch-merge-core-service"
    );

    // 1. 构建 PatchMergeCore。
    let config = build_config_from_env();
    let core = PatchMergeCore::new(config);

    // 2. 先 spawn HTTP 服务（独立 task），确保 /healthz 立即可用——这对 K8s
    //    liveness/readiness 探针至关重要：若 NATS 短暂不可用，重试循环会阻塞
    //    ~10s，此时探针若拿不到 /healthz 会判定 Pod 不健康并触发重启循环。
    let server = PatchMergeHttpServer::new(Arc::clone(&core), addr);
    let server_handle = tokio::spawn(async move {
        if let Err(e) = server.serve().await {
            tracing::error!(error = %e, "http server error");
        }
    });

    // 3. 在主 future 里连接 NATS 并订阅。主 future 由 block_on 驱动，不要求 Send，
    //    故 retry_connect_nats 的非 Send 错误类型（Box<dyn Error>）无碍。
    let nats_core = Arc::clone(&core);
    let nats_adapter: Option<Arc<NatsAdapter>> =
        match retry_connect_nats(&nats_url, 5, Duration::from_secs(2)).await {
            Ok(adapter) => {
                let arc = Arc::new(adapter);
                let sub_core = Arc::clone(&nats_core);
                if let Err(e) = Arc::clone(&arc).spawn_subscription(sub_core).await {
                    tracing::error!(error = %e, "failed to spawn NATS subscription");
                }
                Some(arc)
            }
            Err(e) => {
                tracing::warn!(
                    error = %e,
                    "NATS unavailable; patch-merge-core running in HTTP-only mode (no event ingestion)"
                );
                None
            }
        };

    // 4. 周期性统计日志。
    let stats_core = Arc::clone(&core);
    let stats_handle = tokio::spawn(async move {
        let mut tick = tokio::time::interval(Duration::from_secs(60));
        tick.tick().await; // 跳过立即触发
        loop {
            tick.tick().await;
            let s = stats_core.stats().await;
            tracing::info!(
                diffs = s.diffs_total,
                patches = s.patches_total,
                patches_failed = s.patches_failed,
                merges = s.merges_total,
                merges_with_conflicts = s.merges_with_conflicts,
                conflicts_total = s.conflicts_total,
                "patch-merge-core stats snapshot"
            );
        }
    });

    // 5. 等待 Ctrl+C。
    match tokio::signal::ctrl_c().await {
        Ok(_) => tracing::info!("ctrl-c received, shutting down"),
        Err(e) => tracing::warn!(error = %e, "failed to install ctrl-c handler"),
    }

    // 6. 优雅停机。
    server_handle.abort();
    stats_handle.abort();
    drop(nats_adapter);

    tracing::info!("patch-merge-core-service stopped");
    Ok(())
}

fn init_tracing() {
    use tracing_subscriber::{fmt, EnvFilter};
    let filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("info,patch_merge_core=info"));
    let _ = fmt().with_env_filter(filter).with_target(false).try_init();
}

fn build_config_from_env() -> DiffConfig {
    DiffConfig {
        max_text_bytes: getenv_usize("PMC_MAX_TEXT_BYTES", 8 * 1024 * 1024),
        max_ops: getenv_usize("PMC_MAX_OPS", 200_000),
    }
}

async fn retry_connect_nats(
    url: &str,
    max_attempts: u32,
    delay: Duration,
) -> Result<NatsAdapter, Box<dyn std::error::Error>> {
    // 本函数在 main future（block_on 驱动，不要求 Send）中调用，故错误类型
    // 用 Box<dyn Error>（非 Send）即可。
    let mut last_err: Option<Box<dyn std::error::Error>> = None;
    for attempt in 1..=max_attempts {
        let mut adapter = NatsAdapter::new();
        match adapter.connect(url).await {
            Ok(()) => {
                tracing::info!(attempt, "connected to NATS");
                return Ok(adapter);
            }
            Err(e) => {
                tracing::warn!(attempt, error = %e, "NATS connect failed, retrying");
                last_err = Some(e);
                tokio::time::sleep(delay).await;
            }
        }
    }
    Err(last_err.unwrap_or_else(|| "unknown NATS connect error".into()))
}

fn getenv_or(key: &str, default: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| default.to_string())
}

fn getenv_usize(key: &str, default: usize) -> usize {
    std::env::var(key)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}
