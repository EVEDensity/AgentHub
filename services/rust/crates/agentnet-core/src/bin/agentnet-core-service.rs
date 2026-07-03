//! agentnet-core-service main binary.
//!
//! Startup sequence:
//! 1. Initialize tracing (tracing + env-filter).
//! 2. Build [`AgentNetConfig`] from environment, create [`DagEngine`] and [`AgentRegistry`].
//! 3. **Spawn HTTP service first** (/healthz, /stats, /dags, /agents) — ensures
//!    K8s liveness/readiness probes are available immediately, even if NATS is retrying.
//! 4. Connect NATS in the main future and subscribe to agentnet subjects.
//! 5. Periodic stats logging.
//! 6. Graceful shutdown on Ctrl+C.
//!
//! Environment variables:
//! - `NATS_URL` (default `nats://127.0.0.1:4222`)
//! - `AGENTNET_CORE_ADDR` (default `0.0.0.0:8107`)
//! - `RUST_LOG` (default `info,agentnet_core=info`)
//! - `AN_MAX_CONCURRENT_DAGS` (default 100)
//! - `AN_MAX_NODES_PER_DAG` (default 500)
//! - `AN_DEFAULT_STRATEGY` (default `capability-match`)
//! - `AN_CAPABILITY_REFRESH_SECS` (default 30)
//! - `AN_STATS_TICK_SECS` (default 30)

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use agentnet_core::core::{AgentRegistry, AssignmentPolicy, DagEngine, TaskAssigner};
use agentnet_core::nats::{retry_connect_nats, NatsAgentNetAdapter};
use agentnet_core::server::AgentNetHttpServer;
use agentnet_core::types::{AgentNetConfig, AssignmentStrategy};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    init_tracing();

    let addr: SocketAddr = getenv_or("AGENTNET_CORE_ADDR", "0.0.0.0:8107")
        .parse()
        .expect("invalid AGENTNET_CORE_ADDR");
    let nats_url = getenv_or("NATS_URL", "nats://127.0.0.1:4222");

    tracing::info!(
        version = agentnet_core::VERSION,
        addr = %addr,
        nats_url = %nats_url,
        "starting agentnet-core-service"
    );

    // 1. Build engine and registry.
    let config = build_config_from_env();
    let engine = DagEngine::new(config);
    let registry = Arc::new(AgentRegistry::new());

    // 2. Spawn HTTP service first — ensures /healthz is available before NATS connect.
    let server = AgentNetHttpServer::new(engine.clone(), registry.clone(), addr);
    let server_handle = tokio::spawn(async move {
        if let Err(e) = server.serve().await {
            tracing::error!(error = %e, "http server error");
        }
    });

    // 3. Connect NATS and spawn subscriptions.
    let nats_adapter: Option<Arc<NatsAgentNetAdapter>> =
        match retry_connect_nats(&nats_url, 5, Duration::from_secs(2)).await {
            Ok(adapter) => {
                let arc = Arc::new(adapter);
                // Spawn capability subscription
                if let Err(e) = Arc::clone(&arc)
                    .spawn_capability_subscription(registry.clone())
                    .await
                {
                    tracing::error!(error = %e, "failed to spawn capability subscription");
                }
                // Spawn heartbeat subscription
                if let Err(e) = Arc::clone(&arc)
                    .spawn_heartbeat_subscription(registry.clone())
                    .await
                {
                    tracing::error!(error = %e, "failed to spawn heartbeat subscription");
                }
                // Spawn memory subscription
                if let Err(e) = Arc::clone(&arc).spawn_memory_subscription().await {
                    tracing::error!(error = %e, "failed to spawn memory subscription");
                }
                Some(arc)
            }
            Err(e) => {
                tracing::warn!(
                    error = %e,
                    "NATS unavailable; agentnet-core running in HTTP-only mode"
                );
                None
            }
        };

    // 4. Periodic stats logging.
    let stats_engine = engine.clone();
    let stats_handle = tokio::spawn(async move {
        let mut tick = tokio::time::interval(Duration::from_secs(60));
        tick.tick().await; // Skip immediate trigger
        loop {
            tick.tick().await;
            let stats = stats_engine.stats().await;
            let dag_count = stats_engine.dag_count().await;
            tracing::info!(
                active_dags = stats.active_dags,
                total_tasks = stats.total_tasks,
                total_dags = dag_count,
                "agentnet-core stats snapshot"
            );
        }
    });

    // 5. Wait for Ctrl+C.
    match tokio::signal::ctrl_c().await {
        Ok(_) => tracing::info!("ctrl-c received, shutting down"),
        Err(e) => tracing::warn!(error = %e, "failed to install ctrl-c handler"),
    }

    // 6. Graceful shutdown.
    server_handle.abort();
    stats_handle.abort();
    drop(nats_adapter);

    tracing::info!("agentnet-core-service stopped");
    Ok(())
}

fn init_tracing() {
    use tracing_subscriber::{fmt, EnvFilter};
    let filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("info,agentnet_core=info"));
    let _ = fmt().with_env_filter(filter).with_target(false).try_init();
}

fn build_config_from_env() -> AgentNetConfig {
    AgentNetConfig {
        max_concurrent_dags: getenv_usize("AN_MAX_CONCURRENT_DAGS", 100),
        max_nodes_per_dag: getenv_usize("AN_MAX_NODES_PER_DAG", 500),
        default_strategy: parse_strategy(&getenv_or(
            "AN_DEFAULT_STRATEGY",
            "capability-match",
        )),
        capability_refresh_secs: getenv_u64("AN_CAPABILITY_REFRESH_SECS", 30),
        stats_tick_secs: getenv_u64("AN_STATS_TICK_SECS", 30),
    }
}

fn parse_strategy(s: &str) -> AssignmentStrategy {
    match s.trim().to_ascii_lowercase().as_str() {
        "round-robin" => AssignmentStrategy::RoundRobin,
        "least-loaded" => AssignmentStrategy::LeastLoaded,
        "cost-optimized" => AssignmentStrategy::CostOptimized,
        _ => AssignmentStrategy::CapabilityMatch,
    }
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

fn getenv_u64(key: &str, default: u64) -> u64 {
    std::env::var(key)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}
