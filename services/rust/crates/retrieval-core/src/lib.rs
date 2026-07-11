//! # retrieval-core
//!
//! AgentHub 平台的高性能检索核心，提供 hybrid retrieve + 融合 + rerank 能力：
//!
//! 1. **多源检索**（[`qdrant::QdrantClient`] / [`opensearch::OpenSearchClient`]）：
//!    并行查询 Qdrant（dense 向量）与 OpenSearch（BM25 稀疏），各自带超时与降级。
//! 2. **融合引擎**（[`fusion::FusionEngine`]）：RRF + 加权 + freshness 衰减 +
//!    content_hash 去重 + rerank 混合，输出 citation-ready 的排序列表。
//! 3. **Rerank**（[`model_adapter::ModelAdapterClient`]）：调 model-adapter `/v1/rerank`
//!    对 top-N 候选精排；超时或 404 时降级为 "fusion score only"。
//! 4. **编排**（[`core::RetrievalCore`]）：串联 embedding → 并行检索 → 融合 → rerank
//!    → citation 生成，并维护运行时统计与降级计数。
//!
//! 接入方式：通过 NATS 订阅 `agenthub.retrieval.query`（事件
//! `retrieval.query.requested`），处理后发布 `retrieval.fusion.completed` 与
//! `retrieval.query.completed` 到 `agenthub.retrieval.fusion`。详见 [`nats::NatsAdapter`]。
//!
//! 降级链（对照 `react_deepsearch_flow.json` 的 fallbacks）：
//! - BM25 超时 → `Bm25Only`（仅用 dense）
//! - Dense 超时 → `DenseOnly`（仅用 BM25）
//! - Embedding 超时 → 无法做 dense，降级为 `Bm25Only`
//! - Rerank 超时/不可用 → `FusionScoreOnly`（仅用融合分数）

pub mod core;
pub mod fusion;
pub mod model_adapter;
pub mod nats;
pub mod opensearch;
pub mod qdrant;
pub mod server;
pub mod types;

// ── 顶层 re-export（常用类型直接可 `use retrieval_core::X`）────────────
pub use core::{RetrievalCore, RetrievalCoreConfig, RetrievalCoreHealth, RetrievalCoreStats};
pub use fusion::{AtomicF64, DynamicWeights, FusionConfig, FusionEngine, FusionWeights};
pub use model_adapter::ModelAdapterClient;
pub use opensearch::OpenSearchClient;
pub use qdrant::QdrantClient;
pub use types::{
    Citation, Degradation, FusionResult, FusedCandidate, QueryCompleted, RetrievalCandidate,
    RetrievalRequest, SourceType,
};

/// Crate 版本（与 Cargo workspace 一致）。
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
