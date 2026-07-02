//! fanout-core-service 主程序。
//!
//! 启动流程：
//! 1. 初始化日志（tracing + env-filter）。
//! 2. 从环境变量构建 [`FanoutConfig`]，组装 [`FanoutCore`]。
//! 3. **先 spawn HTTP 服务**（/healthz, /metrics, /stats, /subscribers, /channels,
//!    /partitions, POST /route），确保 K8s liveness/readiness 探针立即可用——
//!    即使 NATS 还在重试，/healthz 已可响应。
//! 4. 在主 future 里连接 NATS 并订阅 `agenthub.fanout.events`，把 envelope 解析为
//!    [`FanoutEvent`] 后调 `core.route()`，回执发布到 `agenthub.fanout.audit`。
//!    主 future 由 block_on 驱动不要求 Send，故 NATS 重试（含非 Send 错误类型）
//!    不阻塞独立的 HTTP task。
//! 5. 周期性统计日志（便于监控订阅者规模与分区状态）。
//! 6. 监听 Ctrl+C 优雅停机。
//!
//! 环境变量：
//! - `NATS_URL`（默认 `nats://127.0.0.1:4222`）
//! - `FANOUT_CORE_ADDR`（默认 `0.0.0.0:8103`）
//! - `RUST_LOG`（默认 `info,fanout_core=info`）
//! - `FC_INITIAL_PARTITIONS`（默认 8）
//! - `FC_MAX_PARTITIONS`（默认 32）
//! - `FC_PARTITION_SCALE_THRESHOLD`（默认 1000）
//! - `FC_SUBSCRIBER_CAPACITY`（默认 256）
//! - `FC_SLOW_SUBSCRIBER_POLICY`（drop_oldest|drop_newest|coalesce，默认 drop_oldest）

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use fanout_core::core::FanoutCore;
use fanout_core::nats::NatsAdapter;
use fanout_core::server::FanoutHttpServer;
use fanout_core::types::{FanoutConfig, SlowSubscriberPolicy};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    init_tracing();

    let addr: SocketAddr = getenv_or("FANOUT_CORE_ADDR", "0.0.0.0:8103")
        .parse()
        .expect("invalid FANOUT_CORE_ADDR");
    let nats_url = getenv_or("NATS_URL", "nats://127.0.0.1:4222");

    tracing::info!(
        version = fanout_core::VERSION,
        addr = %addr,
        nats_url = %nats_url,
        "starting fanout-core-service"
    );

    // 1. 构建 FanoutCore。
    let config = build_config_from_env();
    let core = FanoutCore::new(config);

    // 2. 先 spawn HTTP 服务（独立 task，FanoutHttpServer 满足 Send），确保 /healthz
    //    立即可用——这对 K8s liveness/readiness 探针至关重要：若 NATS 短暂不可用，
    //    重试循环会阻塞 ~10s，此时探针若拿不到 /healthz 会判定 Pod 不健康并触发
    //    重启循环。把 HTTP 提前到 NATS 之前，即使 NATS 还在重试，/healthz 也已可响应。
    let server = FanoutHttpServer::new(Arc::clone(&core), addr);
    let server_handle = tokio::spawn(async move {
        if let Err(e) = server.serve().await {
            tracing::error!(error = %e, "http server error");
        }
    });

    // 3. 在主 future 里连接 NATS 并订阅。主 future 由 block_on 驱动，不要求 Send，
    //    故 retry_connect_nats 的非 Send 错误类型（Box<dyn Error>）无碍。HTTP 服务
    //    已在独立 task 中运行，此处 NATS 重试不阻塞 /healthz。
    let nats_core = Arc::clone(&core);
    let nats_adapter: Option<Arc<NatsAdapter>> =
        match retry_connect_nats(&nats_url, 5, Duration::from_secs(2)).await {
            Ok(adapter) => {
                let arc = Arc::new(adapter);
                let sub_core = Arc::clone(&nats_core);
                // spawn_subscription 消耗 Arc<Self>，故传 clone，保留原 arc 供停机时 drop。
                if let Err(e) = Arc::clone(&arc).spawn_subscription(sub_core).await {
                    tracing::error!(error = %e, "failed to spawn NATS subscription");
                }
                Some(arc)
            }
            Err(e) => {
                tracing::warn!(
                    error = %e,
                    "NATS unavailable; fanout-core running in HTTP-only mode (no event ingestion)"
                );
                None
            }
        };

    // 4. 周期性统计日志（便于监控订阅者规模与分区状态）。
    let stats_core = Arc::clone(&core);
    let stats_handle = tokio::spawn(async move {
        let mut tick = tokio::time::interval(Duration::from_secs(60));
        tick.tick().await; // 跳过立即触发
        loop {
            tick.tick().await;
            let s = stats_core.stats().await;
            tracing::info!(
                events_total = s.events_total,
                events_delivered = s.events_delivered,
                events_dropped = s.events_dropped,
                active_subscribers = s.active_subscribers,
                active_channels = s.active_channels,
                partitions = s.partition_count,
                degraded_total = s.degraded_total,
                "fanout-core stats snapshot"
            );
        }
    });

    // 5. 等待 Ctrl+C。
    match tokio::signal::ctrl_c().await {
        Ok(_) => tracing::info!("ctrl-c received, shutting down"),
        Err(e) => tracing::warn!(error = %e, "failed to install ctrl-c handler"),
    }

    // 6. 优雅停机。abort HTTP/stats task；drop nats_adapter（若存在）会断开 NATS。
    server_handle.abort();
    stats_handle.abort();
    drop(nats_adapter);

    tracing::info!("fanout-core-service stopped");
    Ok(())
}

fn init_tracing() {
    use tracing_subscriber::{fmt, EnvFilter};
    let filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("info,fanout_core=info"));
    let _ = fmt().with_env_filter(filter).with_target(false).try_init();
}

fn build_config_from_env() -> FanoutConfig {
    FanoutConfig {
        initial_partitions: getenv_usize("FC_INITIAL_PARTITIONS", 8),
        max_partitions: getenv_usize("FC_MAX_PARTITIONS", 32),
        partition_scale_threshold: getenv_usize("FC_PARTITION_SCALE_THRESHOLD", 1000),
        subscriber_capacity: getenv_usize("FC_SUBSCRIBER_CAPACITY", 256),
        slow_subscriber_policy: parse_policy(&getenv_or(
            "FC_SLOW_SUBSCRIBER_POLICY",
            "drop_oldest",
        )),
        stats_tick: Duration::from_secs(30),
    }
}

fn parse_policy(s: &str) -> SlowSubscriberPolicy {
    match s.trim().to_ascii_lowercase().as_str() {
        "drop_newest" => SlowSubscriberPolicy::DropNewest,
        "coalesce" => SlowSubscriberPolicy::Coalesce,
        _ => SlowSubscriberPolicy::DropOldest,
    }
}

async fn retry_connect_nats(
    url: &str,
    max_attempts: u32,
    delay: Duration,
) -> Result<NatsAdapter, Box<dyn std::error::Error>> {
    // 本函数在 main future（block_on 驱动，不要求 Send）中调用，故错误类型
    // 用 Box<dyn Error>（非 Send）即可。若未来需在 tokio::spawn 中调用，
    // 再改为 Box<dyn Error + Send + Sync> 并把 last_err 存为 String。
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
