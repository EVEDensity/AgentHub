//! retrieval-core-service 主程序。
//!
//! 启动流程：
//! 1. 初始化日志（tracing + env-filter）。
//! 2. 从环境变量构建下游客户端（Qdrant / OpenSearch / model-adapter）与
//!    [`RetrievalCoreConfig`]，组装 [`RetrievalCore`]。
//! 3. **先 spawn HTTP 服务**（/healthz, /metrics, /stats, /health/details, POST /retrieve），
//!    确保 K8s liveness/readiness 探针立即可用——即使 NATS 还在重试，/healthz 已可响应。
//! 4. 在主 future 里连接 NATS 并订阅 `agenthub.retrieval.query`，把 envelope 解析为
//!    [`RetrievalRequest`] 后调 `core.retrieve()`，结果发布到
//!    `agenthub.retrieval.fusion`。主 future 由 block_on 驱动不要求 Send，故 NATS
//!    重试（含非 Send 错误类型）不阻塞独立的 HTTP task。
//! 5. 监听 Ctrl+C 优雅停机。
//!
//! 环境变量：
//! - `NATS_URL`（默认 `nats://127.0.0.1:4222`）
//! - `RETRIEVAL_CORE_ADDR`（默认 `0.0.0.0:8102`）
//! - `RUST_LOG`（默认 `info,retrieval_core=info`）
//! - `QDRANT_URL`（默认 `http://127.0.0.1:6333`）
//! - `OPENSEARCH_URL`（默认 `http://127.0.0.1:9200`）
//! - `MODEL_ADAPTER_URL`（默认 `http://127.0.0.1:8091`）
//! - `EMBEDDING_MODEL`（默认 `bge-large-zh-v1.5`）
//! - `RC_SOURCE_TIMEOUT_MS`（默认 500）
//! - `RC_EMBEDDING_TIMEOUT_MS`（默认 300）
//! - `RC_RERANK_TIMEOUT_MS`（默认 400）
//! - `RC_RERANK_TOP_N`（默认 20）
//! - `RC_RERANK_ENABLED`（默认 `true`，置 `false` 关闭 rerank）
//! - `RC_FUSION_PER_SOURCE_LIMIT`（默认 50）
//! - `RC_FUSION_RRF_K`（默认 60）
//! - `RC_FUSION_FRESHNESS_HALF_LIFE_DAYS`（默认 30）
//! - `RC_HTTP_TIMEOUT_MS`（默认 2000，下游 HTTP 客户端整体超时）

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use retrieval_core::core::{RetrievalCore, RetrievalCoreConfig};
use retrieval_core::fusion::{DynamicWeights, FusionConfig};
use retrieval_core::model_adapter::ModelAdapterClient;
use retrieval_core::nats::NatsAdapter;
use retrieval_core::opensearch::OpenSearchClient;
use retrieval_core::qdrant::QdrantClient;
use retrieval_core::server::RetrievalHttpServer;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    init_tracing();

    let addr: SocketAddr = getenv_or("RETRIEVAL_CORE_ADDR", "0.0.0.0:8102")
        .parse()
        .expect("invalid RETRIEVAL_CORE_ADDR");
    let nats_url = getenv_or("NATS_URL", "nats://127.0.0.1:4222");
    let qdrant_url = getenv_or("QDRANT_URL", "http://127.0.0.1:6333");
    let opensearch_url = getenv_or("OPENSEARCH_URL", "http://127.0.0.1:9200");
    let model_adapter_url = getenv_or("MODEL_ADAPTER_URL", "http://127.0.0.1:8091");
    let embedding_model = getenv_or("EMBEDDING_MODEL", "bge-large-zh-v1.5");

    tracing::info!(
        version = retrieval_core::VERSION,
        addr = %addr,
        nats_url = %nats_url,
        qdrant_url = %qdrant_url,
        opensearch_url = %opensearch_url,
        model_adapter_url = %model_adapter_url,
        "starting retrieval-core-service"
    );

    // 1. 构建下游客户端。HTTP 客户端整体超时取 RC_HTTP_TIMEOUT_MS，
    //    单源/嵌入/rerank 超时由 RetrievalCore 内部 tokio::time::timeout 控制。
    let http_timeout = Duration::from_millis(getenv_u64("RC_HTTP_TIMEOUT_MS", 2000));
    let qdrant = QdrantClient::new(&qdrant_url, http_timeout);
    let opensearch = OpenSearchClient::new(&opensearch_url, http_timeout);
    let model_adapter = ModelAdapterClient::new(&model_adapter_url, &embedding_model, http_timeout);

    // 2. 构建 RetrievalCore（含动态权重）。
    let (config, weights) = build_config_from_env();
    let core = RetrievalCore::new(config, qdrant, opensearch, model_adapter, weights);

    // 3. 先 spawn HTTP 服务（独立 task，RetrievalHttpServer 满足 Send），确保 /healthz
    //    立即可用——这对 K8s liveness/readiness 探针至关重要：若 NATS 短暂不可用，
    //    重试循环会阻塞 ~10s，此时探针若拿不到 /healthz 会判定 Pod 不健康并触发
    //    重启循环。把 HTTP 提前到 NATS 之前，即使 NATS 还在重试，/healthz 也已可响应。
    let server = RetrievalHttpServer::new(Arc::clone(&core), addr);
    let server_handle = tokio::spawn(async move {
        if let Err(e) = server.serve().await {
            tracing::error!(error = %e, "http server error");
        }
    });

    // 4. 在主 future 里连接 NATS 并订阅。主 future 由 block_on 驱动，不要求 Send，
    //    故 retry_connect_nats 的非 Send 错误类型（Box<dyn Error>）无碍。HTTP 服务
    //    已在独立 task 中运行，此处 NATS 重试不阻塞 /healthz。
    let nats_core = Arc::clone(&core);
    let nats_adapter: Option<Arc<NatsAdapter>> = match retry_connect_nats(&nats_url, 5, Duration::from_secs(2)).await {
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
                "NATS unavailable; retrieval-core running in HTTP-only mode (no event ingestion)"
            );
            None
        }
    };

    // 5. 周期性健康探测日志（便于排查下游故障）。
    let health_core = Arc::clone(&core);
    let health_handle = tokio::spawn(async move {
        let mut tick = tokio::time::interval(Duration::from_secs(30));
        tick.tick().await; // 跳过立即触发
        loop {
            tick.tick().await;
            let h = health_core.health().await;
            if !h.all_healthy() {
                tracing::warn!(
                    qdrant = h.qdrant,
                    opensearch = h.opensearch,
                    model_adapter = h.model_adapter,
                    "downstream dependency unhealthy"
                );
            }
        }
    });

    // 6. 等待 Ctrl+C。
    match tokio::signal::ctrl_c().await {
        Ok(_) => tracing::info!("ctrl-c received, shutting down"),
        Err(e) => tracing::warn!(error = %e, "failed to install ctrl-c handler"),
    }

    // 7. 优雅停机。abort HTTP/health task；drop nats_adapter（若存在）会断开 NATS。
    server_handle.abort();
    health_handle.abort();
    drop(nats_adapter);

    tracing::info!("retrieval-core-service stopped");
    Ok(())
}

fn init_tracing() {
    use tracing_subscriber::{fmt, EnvFilter};
    let filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("info,retrieval_core=info"));
    let _ = fmt().with_env_filter(filter).with_target(false).try_init();
}

fn build_config_from_env() -> (RetrievalCoreConfig, Arc<DynamicWeights>) {
    let weights = Arc::new(DynamicWeights::from_env_or_default());
    let fusion = FusionConfig {
        rrf_k: getenv_u32("RC_FUSION_RRF_K", 60),
        freshness_half_life_days: getenv_f64("RC_FUSION_FRESHNESS_HALF_LIFE_DAYS", 30.0),
        per_source_limit: getenv_usize("RC_FUSION_PER_SOURCE_LIMIT", 50),
    };
    let config = RetrievalCoreConfig {
        fusion,
        source_timeout: Duration::from_millis(getenv_u64("RC_SOURCE_TIMEOUT_MS", 500)),
        embedding_timeout: Duration::from_millis(getenv_u64("RC_EMBEDDING_TIMEOUT_MS", 300)),
        rerank_timeout: Duration::from_millis(getenv_u64("RC_RERANK_TIMEOUT_MS", 400)),
        rerank_top_n: getenv_usize("RC_RERANK_TOP_N", 20),
        rerank_enabled: parse_bool(&getenv_or("RC_RERANK_ENABLED", "true")),
    };
    (config, weights)
}

fn parse_bool(s: &str) -> bool {
    matches!(
        s.trim().to_ascii_lowercase().as_str(),
        "true" | "1" | "yes" | "on"
    )
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

fn getenv_u64(key: &str, default: u64) -> u64 {
    std::env::var(key)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}

fn getenv_u32(key: &str, default: u32) -> u32 {
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

fn getenv_f64(key: &str, default: f64) -> f64 {
    std::env::var(key)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}
