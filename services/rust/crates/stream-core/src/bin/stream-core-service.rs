//! stream-core-service 主程序。
//!
//! 启动流程：
//! 1. 初始化日志（tracing + env-filter）。
//! 2. 从环境变量构建 [`StreamCoreConfig`]。
//! 3. 创建 [`StreamCore`] 并 spawn 后台驱动循环（ingest / interval flush / reaper）。
//! 4. 连接 NATS，订阅 `agenthub.session.stream.events`，把 envelope 推入 StreamCore。
//! 5. 启动 HTTP 服务（/healthz, /metrics, /stats, /consumers, /streams/sse, ...）。
//! 6. 监听 Ctrl+C 优雅停机：flush 残留缓冲区 → 关闭连接。
//!
//! 环境变量：
//! - `NATS_URL`（默认 `nats://127.0.0.1:4222`）
//! - `STREAM_CORE_ADDR`（默认 `0.0.0.0:8101`）
//! - `RUST_LOG`（默认 `info,stream_core=info`）
//! - `SC_FLUSH_MAX_CHUNKS`（默认 12）
//! - `SC_FLUSH_MAX_INTERVAL_MS`（默认 120）
//! - `SC_FLUSH_MAX_BYTES`（默认 8192）
//! - `SC_BP_CAPACITY`（默认 1024）
//! - `SC_BP_POLICY`（block|drop_oldest|drop_newest|coalesce，默认 drop_oldest）
//! - `SC_CONSUMER_CAPACITY`（默认 64）
//! - `SC_CONSUMER_LAG_THRESHOLD`（默认 16）
//! - `SC_CONSUMER_POLICY`（默认 drop_oldest）
//! - `SC_CONSUMER_IDLE_TIMEOUT_SECS`（默认 90）

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use stream_core::backpressure::{BackpressureConfig, FullAction};
use stream_core::consumer::{ConsumerConfig, SlowConsumerPolicy};
use stream_core::core::{StreamCore, StreamCoreConfig};
use stream_core::merger::FlushPolicy;
use stream_core::nats::NatsAdapter;
use stream_core::server::StreamHttpServer;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    init_tracing();

    let config = build_config_from_env();
    let addr: SocketAddr = getenv_or("STREAM_CORE_ADDR", "0.0.0.0:8101")
        .parse()
        .expect("invalid STREAM_CORE_ADDR");
    let nats_url = getenv_or("NATS_URL", "nats://127.0.0.1:4222");

    tracing::info!(version = stream_core::VERSION, addr = %addr, nats_url = %nats_url, "starting stream-core-service");

    // 1. 创建 StreamCore 并 spawn 后台循环。
    let core = StreamCore::new(config);
    let core_handles = Arc::clone(&core).spawn();

    // 2. 连接 NATS 并订阅（失败则重试，不阻断启动——HTTP 仍可服务）。
    let nats_adapter = match retry_connect_nats(&nats_url, 5, Duration::from_secs(2)).await {
        Ok(a) => {
            let a = Arc::new(a);
            let sub_core = Arc::clone(&core);
            if let Err(e) = a.spawn_subscription(sub_core).await {
                tracing::error!(error = %e, "failed to spawn NATS subscription");
            }
            Some(a)
        }
        Err(e) => {
            tracing::warn!(error = %e, "NATS unavailable; stream-core running in HTTP-only mode (no upstream ingestion)");
            None
        }
    };

    // 3. 启动 HTTP 服务。
    let server = StreamHttpServer::new(Arc::clone(&core), addr);
    let server_handle = tokio::spawn(async move {
        if let Err(e) = server.serve().await {
            tracing::error!(error = %e, "http server error");
        }
    });

    // 4. 等待 Ctrl+C。
    match tokio::signal::ctrl_c().await {
        Ok(_) => tracing::info!("ctrl-c received, shutting down"),
        Err(e) => tracing::warn!(error = %e, "failed to install ctrl-c handler"),
    }

    // 5. 优雅停机：flush 残留缓冲区。
    tracing::info!("flushing residual buffers before shutdown");
    core.shutdown_flush().await;

    // 6. 取消后台 task。
    for h in core_handles {
        h.abort();
    }
    server_handle.abort();
    if nats_adapter.is_some() {
        // nats_adapter 是 Arc，drop 即可；显式 close 不持有所有权。
        tracing::info!("nats adapter will be dropped on exit");
    }

    tracing::info!("stream-core-service stopped");
    Ok(())
}

fn init_tracing() {
    use tracing_subscriber::{fmt, EnvFilter};
    let filter =
        EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info,stream_core=info"));
    let _ = fmt().with_env_filter(filter).with_target(false).try_init();
}

fn build_config_from_env() -> StreamCoreConfig {
    let flush_policy = FlushPolicy {
        max_buffered_chunks: getenv_usize("SC_FLUSH_MAX_CHUNKS", 12),
        max_flush_interval: Duration::from_millis(getenv_u64("SC_FLUSH_MAX_INTERVAL_MS", 120)),
        max_merged_bytes: getenv_usize("SC_FLUSH_MAX_BYTES", 8 * 1024),
    };
    let backpressure = BackpressureConfig {
        capacity: getenv_usize("SC_BP_CAPACITY", 1024),
        full_action: parse_bp_policy(&getenv_or("SC_BP_POLICY", "drop_oldest")),
        block_timeout: Duration::from_millis(50),
    };
    let consumer = ConsumerConfig {
        capacity: getenv_usize("SC_CONSUMER_CAPACITY", 64),
        lag_threshold: getenv_usize("SC_CONSUMER_LAG_THRESHOLD", 16),
        policy: parse_consumer_policy(&getenv_or("SC_CONSUMER_POLICY", "drop_oldest")),
        idle_timeout: Duration::from_secs(getenv_u64("SC_CONSUMER_IDLE_TIMEOUT_SECS", 90)),
    };
    StreamCoreConfig {
        flush_policy,
        backpressure,
        consumer,
        flush_tick: Duration::from_millis(30),
        reap_tick: Duration::from_secs(15),
    }
}

fn parse_bp_policy(s: &str) -> FullAction {
    match s.to_ascii_lowercase().as_str() {
        "block" => FullAction::Block,
        "drop_newest" => FullAction::DropNewest,
        "coalesce" => FullAction::Coalesce,
        _ => FullAction::DropOldest,
    }
}

fn parse_consumer_policy(s: &str) -> SlowConsumerPolicy {
    match s.to_ascii_lowercase().as_str() {
        "coalesce" => SlowConsumerPolicy::Coalesce,
        "drop_newest" => SlowConsumerPolicy::DropNewest,
        "block" => SlowConsumerPolicy::Block,
        _ => SlowConsumerPolicy::DropOldest,
    }
}

async fn retry_connect_nats(
    url: &str,
    max_attempts: u32,
    delay: Duration,
) -> Result<NatsAdapter, async_nats::Error> {
    let mut last_err: Option<async_nats::Error> = None;
    for attempt in 1..=max_attempts {
        match NatsAdapter::connect(url).await {
            Ok(a) => {
                tracing::info!(attempt, "connected to NATS");
                return Ok(a);
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

fn getenv_u64(key: &str, default: u64) -> u64 {
    std::env::var(key)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}

fn getenv_usize(key: &str, default: usize) -> usize {
    std::env::var(key)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}
