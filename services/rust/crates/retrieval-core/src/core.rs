//! RetrievalCore：顶层编排器，串联 embedding → hybrid retrieve → fusion → rerank。
//!
//! ```text
//!   retrieval.query.requested (NATS)
//!        │
//!        ▼
//!   ┌─────────────┐  query text   ┌──────────────┐
//!   │  Embedding   │ ───────────▶ │ model-adapter │
//!   │              │ ◀─────────── │ /v1/embeddings│
//!   └──────┬───────┘  vector      └──────────────┘
//!          │
//!     ┌────┴────┐
//!     ▼         ▼
//!   Qdrant   OpenSearch     (并行 + 各自超时)
//!   (dense)  (BM25)
//!     │         │
//!     └────┬────┘
//!          ▼
//!   ┌───────────┐
//!   │  Fusion   │ (RRF + freshness + dedup)
//!   └─────┬─────┘
//!         │ top-N candidates
//!         ▼
//!   ┌───────────┐
//!   │  Rerank   │ (model-adapter, 超时→降级)
//!   └─────┬─────┘
//!         ▼
//!   FusionResult → citations → NATS publish
//! ```
//!
//! 降级链（对照 `react_deepsearch_flow.json` 的 fallbacks）：
//! - BM25 超时 → `DenseOnly`（仅用 Qdrant 结果）
//! - Dense 超时 → `Bm25Only`（仅用 OpenSearch 结果）
//! - Embedding 超时 → 无法做 dense，降级为 `Bm25Only`
//! - Rerank 超时/不可用 → `FusionScoreOnly`（仅用融合分数）

use std::sync::Arc;
use std::time::{Duration, Instant};

use tokio::sync::Mutex;

use crate::fusion::{FusionConfig, FusionEngine};
use crate::model_adapter::ModelAdapterClient;
use crate::opensearch::OpenSearchClient;
use crate::qdrant::QdrantClient;
use crate::types::{Degradation, FusionResult, RetrievalRequest};

/// RetrievalCore 配置。
#[derive(Debug, Clone)]
pub struct RetrievalCoreConfig {
    pub fusion: FusionConfig,
    /// 单源检索超时（Qdrant / OpenSearch 各自独立）。
    pub source_timeout: Duration,
    /// Embedding 超时。
    pub embedding_timeout: Duration,
    /// Rerank 超时。
    pub rerank_timeout: Duration,
    /// Rerank 输入候选数（融合后取前 N 送 rerank）。
    pub rerank_top_n: usize,
    /// 是否启用 rerank（feature flag，便于灰度）。
    pub rerank_enabled: bool,
}

impl Default for RetrievalCoreConfig {
    fn default() -> Self {
        Self {
            fusion: FusionConfig::default(),
            source_timeout: Duration::from_millis(500),
            embedding_timeout: Duration::from_millis(300),
            rerank_timeout: Duration::from_millis(400),
            rerank_top_n: 20,
            rerank_enabled: true,
        }
    }
}

/// 运行时统计。
#[derive(Debug, Default, Clone, serde::Serialize)]
pub struct RetrievalCoreStats {
    pub queries_total: u64,
    pub queries_succeeded: u64,
    pub queries_failed: u64,
    pub degraded_dense_only: u64,
    pub degraded_bm25_only: u64,
    pub degraded_fusion_score_only: u64,
    pub avg_latency_ms: u64,
    pub total_qdrant_hits: u64,
    pub total_opensearch_hits: u64,
}

/// RetrievalCore：持有所有客户端 + 融合引擎。
pub struct RetrievalCore {
    config: RetrievalCoreConfig,
    qdrant: Arc<QdrantClient>,
    opensearch: Arc<OpenSearchClient>,
    model_adapter: Arc<ModelAdapterClient>,
    fusion: Arc<FusionEngine>,
    stats: Arc<Mutex<RetrievalCoreStats>>,
}

impl RetrievalCore {
    pub fn new(
        config: RetrievalCoreConfig,
        qdrant: QdrantClient,
        opensearch: OpenSearchClient,
        model_adapter: ModelAdapterClient,
    ) -> Arc<Self> {
        Arc::new(Self {
            fusion: Arc::new(FusionEngine::new(config.fusion.clone())),
            qdrant: Arc::new(qdrant),
            opensearch: Arc::new(opensearch),
            model_adapter: Arc::new(model_adapter),
            config,
            stats: Arc::new(Mutex::new(RetrievalCoreStats::default())),
        })
    }

    pub fn config(&self) -> &RetrievalCoreConfig {
        &self.config
    }

    /// 执行一次完整检索。
    pub async fn retrieve(&self, req: &RetrievalRequest) -> FusionResult {
        let start = Instant::now();
        let mut degraded = Vec::new();
        let per_source_limit = self.config.fusion.per_source_limit;

        // 1. Embedding（带超时）。
        let query_vector = tokio::time::timeout(
            self.config.embedding_timeout,
            self.model_adapter.embed(&req.query),
        )
        .await;

        let query_vector = match query_vector {
            Ok(Ok(v)) => Some(v),
            Ok(Err(e)) => {
                tracing::warn!(error = %e, "embedding failed, degrading to BM25 only");
                degraded.push(Degradation::Bm25Only);
                None
            }
            Err(_) => {
                tracing::warn!("embedding timeout, degrading to BM25 only");
                degraded.push(Degradation::Bm25Only);
                None
            }
        };

        // 2. 并行检索 Qdrant + OpenSearch（各自带超时）。
        let scopes = &req.knowledge_scope;

        // Qdrant dense search（若 embedding 成功）。
        let dense_fut = async {
            if let Some(ref vec) = query_vector {
                let mut all = Vec::new();
                for scope in scopes {
                    match self.qdrant.search(scope, vec, per_source_limit, &req.tenant_id).await {
                        Ok(cands) => all.extend(cands),
                        Err(e) => tracing::warn!(scope = %scope, error = %e, "qdrant search failed"),
                    }
                }
                all
            } else {
                Vec::new()
            }
        };

        // OpenSearch BM25 search。
        let bm25_fut = async {
            let mut all = Vec::new();
            for scope in scopes {
                let index = format!("knowledge-{}", scope);
                match self.opensearch.search(&index, &req.query, per_source_limit, &req.tenant_id).await {
                    Ok(cands) => all.extend(cands),
                    Err(e) => tracing::warn!(index = %index, error = %e, "opensearch search failed"),
                }
            }
            all
        };

        let (dense_result, bm25_result) = tokio::join!(
            tokio::time::timeout(self.config.source_timeout, dense_fut),
            tokio::time::timeout(self.config.source_timeout, bm25_fut),
        );

        // 处理超时降级。
        let dense_candidates = match dense_result {
            Ok(cands) => cands,
            Err(_) => {
                if query_vector.is_some() {
                    tracing::warn!("qdrant search timeout, degrading to BM25 only");
                    degraded.push(Degradation::Bm25Only);
                }
                Vec::new()
            }
        };

        let bm25_candidates = match bm25_result {
            Ok(cands) => cands,
            Err(_) => {
                tracing::warn!("opensearch search timeout, degrading to dense only");
                degraded.push(Degradation::DenseOnly);
                Vec::new()
            }
        };

        let qdrant_hits = dense_candidates.len();
        let opensearch_hits = bm25_candidates.len();

        // 3. 初步融合（不含 rerank）→ 取 top-N 送 rerank。
        let intermediate = self.fusion.fuse(
            bm25_candidates.clone(),
            dense_candidates.clone(),
            None,
            self.config.rerank_top_n,
        );

        // 4. Rerank（若启用且候选非空）。
        let mut final_candidates = intermediate.clone();
        if self.config.rerank_enabled && !intermediate.is_empty() {
            let documents: Vec<String> = intermediate.iter().map(|c| c.content.clone()).collect();
            let rerank_result = tokio::time::timeout(
                self.config.rerank_timeout,
                self.model_adapter.rerank(&req.query, &documents, req.top_k),
            )
            .await;

            match rerank_result {
                Ok(Ok(index_scores)) => {
                    // 映射 index → source_id → score。
                    let mut rerank_map: std::collections::HashMap<String, f32> =
                        std::collections::HashMap::new();
                    for (idx, score) in index_scores {
                        if let Some(c) = intermediate.get(idx) {
                            rerank_map.insert(c.source_id.clone(), score);
                        }
                    }
                    // 重新融合（含 rerank 分数）。
                    final_candidates = self.fusion.fuse(
                        bm25_candidates,
                        dense_candidates,
                        Some(&rerank_map),
                        req.top_k,
                    );
                }
                Ok(Err(e)) => {
                    tracing::warn!(error = %e, "rerank failed, using fusion score only");
                    degraded.push(Degradation::FusionScoreOnly);
                    final_candidates.truncate(req.top_k);
                }
                Err(_) => {
                    tracing::warn!("rerank timeout, using fusion score only");
                    degraded.push(Degradation::FusionScoreOnly);
                    final_candidates.truncate(req.top_k);
                }
            }
        } else {
            final_candidates.truncate(req.top_k);
        }

        // 5. 构建 citation 列表 + 结果。
        let citations = FusionResult::citations_from(&final_candidates);
        let elapsed_ms = start.elapsed().as_millis() as u64;

        // 6. 更新统计。
        self.update_stats(
            qdrant_hits,
            opensearch_hits,
            elapsed_ms,
            &degraded,
            true,
        )
        .await;

        FusionResult {
            request_id: req.request_id.clone(),
            strategy: "bm25_dense_rerank_freshness".into(),
            top_k: req.top_k,
            citations,
            candidates: final_candidates,
            qdrant_hits,
            opensearch_hits,
            elapsed_ms,
            degraded: degraded.iter().map(|d| d.as_str().to_string()).collect(),
        }
    }

    /// 健康检查：探测所有下游依赖。
    pub async fn health(&self) -> RetrievalCoreHealth {
        let (qdrant, opensearch, model_adapter) = tokio::join!(
            async { self.qdrant.health().await.unwrap_or(false) },
            async { self.opensearch.health().await.unwrap_or(false) },
            async { self.model_adapter.health().await.unwrap_or(false) },
        );
        RetrievalCoreHealth {
            qdrant,
            opensearch,
            model_adapter,
        }
    }

    pub async fn stats(&self) -> RetrievalCoreStats {
        self.stats.lock().await.clone()
    }

    async fn update_stats(
        &self,
        qdrant_hits: usize,
        opensearch_hits: usize,
        elapsed_ms: u64,
        degraded: &[Degradation],
        succeeded: bool,
    ) {
        let mut s = self.stats.lock().await;
        s.queries_total += 1;
        if succeeded {
            s.queries_succeeded += 1;
        } else {
            s.queries_failed += 1;
        }
        s.total_qdrant_hits += qdrant_hits as u64;
        s.total_opensearch_hits += opensearch_hits as u64;
        for d in degraded {
            match d {
                Degradation::DenseOnly => s.degraded_dense_only += 1,
                Degradation::Bm25Only => s.degraded_bm25_only += 1,
                Degradation::FusionScoreOnly => s.degraded_fusion_score_only += 1,
            }
        }
        // 滑动平均：简化为 cumulative avg。
        let n = s.queries_total;
        s.avg_latency_ms = ((s.avg_latency_ms as u128 * (n - 1) as u128 + elapsed_ms as u128) / n as u128) as u64;
    }
}

/// 健康状态快照。
#[derive(Debug, Clone, serde::Serialize)]
pub struct RetrievalCoreHealth {
    pub qdrant: bool,
    pub opensearch: bool,
    pub model_adapter: bool,
}

impl RetrievalCoreHealth {
    pub fn all_healthy(&self) -> bool {
        self.qdrant && self.opensearch && self.model_adapter
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fusion::FusionConfig;

    #[test]
    fn config_defaults_are_sane() {
        let cfg = RetrievalCoreConfig::default();
        assert!(cfg.source_timeout.as_millis() <= 1000);
        assert!(cfg.rerank_top_n >= 10);
        assert!(cfg.fusion.per_source_limit >= 20);
    }
}
