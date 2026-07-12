//! memory-segment-core-service 主程序。
//!
//! 启动流程：
//! 1. 初始化日志（tracing + env-filter）。
//! 2. 从环境变量构建 [`CompactionConfig`]，组装 [`MemorySegmentCore`]。
//! 3. **先 spawn HTTP 服务**（/healthz, /metrics, /stats, POST /compact /prune /checkpoint），
//!    确保 K8s liveness/readiness 探针立即可用——即使 NATS 还在重试，/healthz 已可响应。
//! 4. 在主 future 里连接 NATS 并订阅 `agenthub.memory.compact.requested`，把 envelope
//!    解析为 `(messages, config)` 后调 `core.compact_with()`，结果发布到
//!    `agenthub.memory.audit`。主 future 由 block_on 驱动不要求 Send，故 NATS 重试
//!    （含非 Send 错误类型）不阻塞独立的 HTTP task。
//! 5. 周期性统计日志。
//! 6. 监听 Ctrl+C 优雅停机。
//!
//! 环境变量：
//! - `NATS_URL`（默认 `nats://127.0.0.1:4222`）
//! - `MEMORY_SEGMENT_CORE_ADDR`（默认 `0.0.0.0:8105`）
//! - `RUST_LOG`（默认 `info,memory_segment_core=info`）
//! - `MSC_COMPACT_TRIGGER_MESSAGES`（默认 40）
//! - `MSC_MAX_TOKENS`（默认 32000）
//! - `MSC_KEEP_RECENT`（默认 10）
//! - `MSC_CHARS_PER_TOKEN`（默认 4.0）

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use memory_segment_core::core::MemorySegmentCore;
use memory_segment_core::nats::NatsAdapter;
use memory_segment_core::server::MemorySegmentHttpServer;
use memory_segment_core::types::CompactionConfig;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    init_tracing();

    let addr: SocketAddr = getenv_or("MEMORY_SEGMENT_CORE_ADDR", "0.0.0.0:8105")
        .parse()
        .expect("invalid MEMORY_SEGMENT_CORE_ADDR");
    let nats_url = getenv_or("NATS_URL", "nats://127.0.0.1:4222");

    tracing::info!(
        version = memory_segment_core::VERSION,
        addr = %addr,
        nats_url = %nats_url,
        "starting memory-segment-core-service"
    );

    // 1. 构建 MemorySegmentCore。
    let config = build_config_from_env();
    let core = MemorySegmentCore::new(config);

    // 2. 先 spawn HTTP 服务（独立 task），确保 /healthz 立即可用——这对 K8s
    //    liveness/readiness 探针至关重要：若 NATS 短暂不可用，重试循环会阻塞
    //    ~10s，此时探针若拿不到 /healthz 会判定 Pod 不健康并触发重启循环。
    let server = MemorySegmentHttpServer::new(Arc::clone(&core), addr);
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
                    "NATS unavailable; memory-segment-core running in HTTP-only mode (no event ingestion)"
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
                compacts = s.compacts_total,
                compacts_triggered = s.compacts_triggered,
                prunes = s.prunes_total,
                checkpoints = s.checkpoints_total,
                messages_compacted = s.messages_compacted_total,
                tokens_reduced = s.tokens_reduced_total,
                "memory-segment-core stats snapshot"
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

    tracing::info!("memory-segment-core-service stopped");
    Ok(())
}

fn init_tracing() {
    use tracing_subscriber::{fmt, EnvFilter};
    let filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("info,memory_segment_core=info"));
    let _ = fmt().with_env_filter(filter).with_target(false).try_init();
}

fn build_config_from_env() -> CompactionConfig {
    CompactionConfig {
        compact_trigger_messages: getenv_usize("MSC_COMPACT_TRIGGER_MESSAGES", 40),
        max_tokens: getenv_usize("MSC_MAX_TOKENS", 32_000),
        keep_recent: getenv_usize("MSC_KEEP_RECENT", 10),
        chars_per_token: getenv_f64("MSC_CHARS_PER_TOKEN", 4.0),
        summary_header: true,
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

fn getenv_f64(key: &str, default: f64) -> f64 {
    std::env::var(key)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}
