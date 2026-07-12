//! 融合引擎：把 BM25 和 dense 两个来源的候选列表融合为单一排序。
//!
//! 算法基于 **Reciprocal Rank Fusion (RRF)** + 加权 + freshness 衰减 + rerank 混合：
//!
//! 1. **RRF**：对每个来源，`rrf_score(d) = 1 / (k + rank(d))`，k=60（标准值）。
//! 2. **加权**：`weighted(d) = Σ w_source * rrf_source(d) / Σ w_source`（仅计入有该候选的来源）。
//! 3. **Freshness**：`freshness(d) = exp(-age_days / half_life)`，half_life=30 天。
//!    最终 `boosted(d) = weighted(d) * (1 + w_freshness * freshness(d))`。
//! 4. **Rerank 混合**（若可用）：`final(d) = w_rerank * rerank(d) + (1 - w_rerank) * boosted(d)`。
//!    若 rerank 不可用：`final(d) = boosted(d)`。
//! 5. **去重**：按 `content_hash` 去重，保留分数最高者。
//! 6. **排序 + top-k**：按 `final` 降序，取前 k 条。
//!
//! 权重可通过环境变量 `RETRIEVAL_WEIGHT_BM25` / `_DENSE` / `_RERANK` / `_FRESHNESS`
//! 配置；未设置时使用默认值。运行时也可通过 `POST /weights` 动态调整。

use std::cmp::Ordering;
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering as AtomicOrdering};
use std::sync::Arc;

use crate::types::{FusedCandidate, RetrievalCandidate};

// ── AtomicF64：用 AtomicU64 存储 f64 位模式的简单原子包装 ────────────

/// 原子 f64 值，线程安全读写。
#[derive(Debug)]
pub struct AtomicF64(AtomicU64);

impl AtomicF64 {
    pub fn new(v: f64) -> Self {
        Self(AtomicU64::new(v.to_bits()))
    }

    pub fn load(&self, order: AtomicOrdering) -> f64 {
        f64::from_bits(self.0.load(order))
    }

    pub fn store(&self, v: f64, order: AtomicOrdering) {
        self.0.store(v.to_bits(), order)
    }

    pub fn swap(&self, v: f64, order: AtomicOrdering) -> f64 {
        f64::from_bits(self.0.swap(v.to_bits(), order))
    }
}

// ── 动态权重（运行时可通过 HTTP 修改）────────────────────────────────

/// 可动态更新的融合权重集。四个权重值各自为原子变量。
#[derive(Debug)]
pub struct DynamicWeights {
    pub bm25: AtomicF64,
    pub dense: AtomicF64,
    pub rerank: AtomicF64,
    pub freshness: AtomicF64,
}

impl DynamicWeights {
    /// 从显式值创建（若任一值非正，回退到默认值）。
    pub fn new(bm25: f64, dense: f64, rerank: f64, freshness: f64) -> Self {
        let total = bm25 + dense + rerank + freshness;
        let (bm25, dense, rerank, freshness) = if total <= 0.0 {
            (0.30, 0.35, 0.25, 0.10)
        } else {
            (bm25, dense, rerank, freshness)
        };
        Self {
            bm25: AtomicF64::new(bm25),
            dense: AtomicF64::new(dense),
            rerank: AtomicF64::new(rerank),
            freshness: AtomicF64::new(freshness),
        }
    }

    /// 从环境变量加载（未设置时使用默认值）。
    pub fn from_env_or_default() -> Self {
        let bm25 = env_f64("RETRIEVAL_WEIGHT_BM25", 0.30);
        let dense = env_f64("RETRIEVAL_WEIGHT_DENSE", 0.35);
        let rerank = env_f64("RETRIEVAL_WEIGHT_RERANK", 0.25);
        let freshness = env_f64("RETRIEVAL_WEIGHT_FRESHNESS", 0.10);
        // 归一化：确保总和为 1.0。
        let total = bm25 + dense + rerank + freshness;
        if total > 0.0 {
            Self {
                bm25: AtomicF64::new(bm25 / total),
                dense: AtomicF64::new(dense / total),
                rerank: AtomicF64::new(rerank / total),
                freshness: AtomicF64::new(freshness / total),
            }
        } else {
            Self::new(0.30, 0.35, 0.25, 0.10)
        }
    }

    /// 读取当前权重快照。
    pub fn snapshot(&self) -> FusionWeights {
        FusionWeights {
            bm25: self.bm25.load(AtomicOrdering::Relaxed) as f32,
            dense: self.dense.load(AtomicOrdering::Relaxed) as f32,
            rerank: self.rerank.load(AtomicOrdering::Relaxed) as f32,
            freshness: self.freshness.load(AtomicOrdering::Relaxed) as f32,
        }
    }

    /// 写入新权重（自动归一化到总和 1.0）。
    pub fn set(&self, bm25: f32, dense: f32, rerank: f32, freshness: f32) {
        let total = bm25 as f64 + dense as f64 + rerank as f64 + freshness as f64;
        if total > 0.0 {
            self.bm25.store(bm25 as f64 / total, AtomicOrdering::Relaxed);
            self.dense.store(dense as f64 / total, AtomicOrdering::Relaxed);
            self.rerank.store(rerank as f64 / total, AtomicOrdering::Relaxed);
            self.freshness.store(freshness as f64 / total, AtomicOrdering::Relaxed);
        }
    }

    /// 序列化为 JSON 值。
    pub fn to_json(&self) -> serde_json::Value {
        let s = self.snapshot();
        serde_json::json!({
            "bm25": s.bm25,
            "dense": s.dense,
            "rerank": s.rerank,
            "freshness": s.freshness,
            "sum": (s.bm25 + s.dense + s.rerank + s.freshness),
        })
    }
}

fn env_f64(key: &str, default: f64) -> f64 {
    std::env::var(key)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}

// ── FusionWeights（只读快照 / 初始构造用）─────────────────────────────

/// 融合权重快照（用于序列化与配置）。
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct FusionWeights {
    pub bm25: f32,
    pub dense: f32,
    pub rerank: f32,
    pub freshness: f32,
}

impl Default for FusionWeights {
    fn default() -> Self {
        Self {
            bm25: 0.30,
            dense: 0.35,
            rerank: 0.25,
            freshness: 0.10,
        }
    }
}

// ── 融合引擎配置 ──────────────────────────────────────────────────────

/// 融合引擎配置。
#[derive(Debug, Clone)]
pub struct FusionConfig {
    /// RRF 常数 k（标准值 60）。
    pub rrf_k: u32,
    /// Freshness 半衰期（天）。
    pub freshness_half_life_days: f64,
    /// 每来源拉取的候选数（融合前）。
    pub per_source_limit: usize,
}

impl Default for FusionConfig {
    fn default() -> Self {
        Self {
            rrf_k: 60,
            freshness_half_life_days: 30.0,
            per_source_limit: 50,
        }
    }
}

// ── 融合引擎 ──────────────────────────────────────────────────────────

/// 融合引擎：持有动态权重 + 静态配置，线程安全。
pub struct FusionEngine {
    config: FusionConfig,
    weights: Arc<DynamicWeights>,
}

impl FusionEngine {
    pub fn new(config: FusionConfig, weights: Arc<DynamicWeights>) -> Self {
        Self { config, weights }
    }

    pub fn config(&self) -> &FusionConfig {
        &self.config
    }

    /// 获取动态权重的 Arc 引用（供 HTTP 端点读写）。
    pub fn weights_arc(&self) -> Arc<DynamicWeights> {
        Arc::clone(&self.weights)
    }

    /// 执行融合。
    ///
    /// - `bm25_candidates`：来自 OpenSearch 的候选（已按分数降序）。
    /// - `dense_candidates`：来自 Qdrant 的候选（已按分数降序）。
    /// - `rerank_scores`：source_id → rerank 分数 [0,1]（若可用）。
    /// - `top_k`：返回前 k 条。
    pub fn fuse(
        &self,
        bm25_candidates: Vec<RetrievalCandidate>,
        dense_candidates: Vec<RetrievalCandidate>,
        rerank_scores: Option<&HashMap<String, f32>>,
        top_k: usize,
    ) -> Vec<FusedCandidate> {
        let w = self.weights.snapshot();
        let k = self.config.rrf_k as f32;

        // 1. 为每个来源构建 source_id → (rank, score, candidate) 索引。
        let bm25_map: HashMap<&str, (usize, f32, &RetrievalCandidate)> = bm25_candidates
            .iter()
            .enumerate()
            .map(|(i, c)| (c.source_id.as_str(), (i + 1, c.score, c)))
            .collect();
        let dense_map: HashMap<&str, (usize, f32, &RetrievalCandidate)> = dense_candidates
            .iter()
            .enumerate()
            .map(|(i, c)| (c.source_id.as_str(), (i + 1, c.score, c)))
            .collect();

        // 2. 收集所有唯一 source_id（按 content_hash 去重前先合并）。
        let mut by_hash: HashMap<String, FusedCandidate> = HashMap::new();

        let merge = |c: &RetrievalCandidate,
                     bm25_rank: Option<usize>,
                     dense_rank: Option<usize>,
                     rerank_scores: Option<&HashMap<String, f32>>,
                     out: &mut HashMap<String, FusedCandidate>| {
            // 加权 RRF：仅计入有该候选的来源。
            let mut weighted_sum = 0.0_f32;
            let mut weight_sum = 0.0_f32;

            let bm25_score = if let Some(rank) = bm25_rank {
                let rrf = 1.0 / (k + rank as f32);
                weighted_sum += w.bm25 * rrf;
                weight_sum += w.bm25;
                Some(rrf)
            } else {
                None
            };

            let dense_score = if let Some(rank) = dense_rank {
                let rrf = 1.0 / (k + rank as f32);
                weighted_sum += w.dense * rrf;
                weight_sum += w.dense;
                Some(rrf)
            } else {
                None
            };

            if weight_sum == 0.0 {
                return;
            }

            let normalized = weighted_sum / weight_sum;

            // Freshness 衰减。
            let freshness = self.freshness_score(c.timestamp);
            let boosted = normalized * (1.0 + w.freshness * freshness);

            // Rerank 混合。
            let rerank_score = rerank_scores.and_then(|m| m.get(&c.source_id).copied());
            let final_score = if let Some(rs) = rerank_score {
                w.rerank * rs + (1.0 - w.rerank) * boosted
            } else {
                boosted
            };

            let fc = FusedCandidate {
                source_id: c.source_id.clone(),
                score: final_score,
                content: c.content.clone(),
                collection: c.collection.clone(),
                bm25_score: bm25_score,
                dense_score: dense_score,
                rerank_score,
                freshness_score: freshness,
                metadata: c.metadata.clone(),
            };

            // 按 content_hash 去重：保留分数最高者。
            let hash = c.content_hash.clone();
            out.entry(hash)
                .and_modify(|existing| {
                    if fc.score > existing.score {
                        *existing = fc.clone();
                    }
                })
                .or_insert_with(|| fc.clone());
        };

        // 遍历所有候选（BM25 + dense），计算融合分数。
        for c in &bm25_candidates {
            let bm25_rank = bm25_map.get(c.source_id.as_str()).map(|(r, _, _)| *r);
            let dense_rank = dense_map.get(c.source_id.as_str()).map(|(r, _, _)| *r);
            merge(c, bm25_rank, dense_rank, rerank_scores, &mut by_hash);
        }
        for c in &dense_candidates {
            // 跳过已在 BM25 中处理的（同 source_id）。
            if bm25_map.contains_key(c.source_id.as_str()) {
                continue;
            }
            let dense_rank = dense_map.get(c.source_id.as_str()).map(|(r, _, _)| *r);
            merge(c, None, dense_rank, rerank_scores, &mut by_hash);
        }

        // 3. 排序 + top-k。
        let mut all: Vec<FusedCandidate> = by_hash.into_values().collect();
        all.sort_by(|a, b| {
            b.score
                .partial_cmp(&a.score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        all.truncate(top_k);
        all
    }

    /// 计算 freshness 衰减系数 [0,1]。
    /// 无时间戳时返回 0（不影响排序）。
    fn freshness_score(&self, ts: Option<chrono::DateTime<chrono::Utc>>) -> f32 {
        let ts = match ts {
            Some(t) => t,
            None => return 0.0,
        };
        let now = chrono::Utc::now();
        let age_secs = (now - ts).num_seconds().max(0) as f64;
        let age_days = age_secs / 86400.0;
        let half_life = self.config.freshness_half_life_days;
        if half_life <= 0.0 {
            return 1.0;
        }
        (-age_days / half_life).exp() as f32
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{content_hash, SourceType};
    use chrono::Utc;

    fn test_weights() -> Arc<DynamicWeights> {
        Arc::new(DynamicWeights::new(0.30, 0.35, 0.25, 0.10))
    }

    fn test_engine() -> FusionEngine {
        FusionEngine::new(FusionConfig::default(), test_weights())
    }

    fn candidate(
        source_id: &str,
        content: &str,
        score: f32,
        source_type: SourceType,
        rank: usize,
    ) -> RetrievalCandidate {
        RetrievalCandidate {
            source_id: source_id.into(),
            source_type,
            collection: "docs".into(),
            content: content.into(),
            score,
            rank,
            content_hash: content_hash(content),
            timestamp: Some(Utc::now()),
            metadata: HashMap::new(),
        }
    }

    #[test]
    fn fuse_merges_bm25_and_dense() {
        let engine = test_engine();
        let bm25 = vec![
            candidate("d1", "alpha", 0.9, SourceType::OpenSearch, 1),
            candidate("d2", "beta", 0.8, SourceType::OpenSearch, 2),
        ];
        let dense = vec![
            candidate("d2", "beta", 0.95, SourceType::Qdrant, 1),
            candidate("d3", "gamma", 0.7, SourceType::Qdrant, 2),
        ];
        let result = engine.fuse(bm25, dense, None, 10);
        // d2 出现在两个来源中，应排第一。
        assert_eq!(result[0].source_id, "d2");
        assert!(result[0].bm25_score.is_some());
        assert!(result[0].dense_score.is_some());
        // d3 仅 dense，d1 仅 bm25。
        assert!(result.len() >= 3);
    }

    #[test]
    fn fuse_dedup_by_content_hash() {
        let engine = test_engine();
        // 两条不同 source_id 但内容相同 → 应去重。
        let bm25 = vec![candidate("d1", "same content", 0.9, SourceType::OpenSearch, 1)];
        let dense = vec![candidate("d2", "same content", 0.95, SourceType::Qdrant, 1)];
        let result = engine.fuse(bm25, dense, None, 10);
        assert_eq!(result.len(), 1);
    }

    #[test]
    fn fuse_respects_top_k() {
        let engine = test_engine();
        let bm25: Vec<_> = (0..10)
            .map(|i| candidate(&format!("b{}", i), &format!("content{}", i), 0.9 - i as f32 * 0.05, SourceType::OpenSearch, i + 1))
            .collect();
        let dense: Vec<_> = (0..10)
            .map(|i| candidate(&format!("d{}", i), &format!("dcontent{}", i), 0.9 - i as f32 * 0.05, SourceType::Qdrant, i + 1))
            .collect();
        let result = engine.fuse(bm25, dense, None, 3);
        assert_eq!(result.len(), 3);
    }

    #[test]
    fn fuse_with_rerank_blends_scores() {
        let engine = test_engine();
        let bm25 = vec![
            candidate("d1", "alpha", 0.9, SourceType::OpenSearch, 1),
            candidate("d2", "beta", 0.8, SourceType::OpenSearch, 2),
        ];
        let dense = vec![candidate("d1", "alpha", 0.95, SourceType::Qdrant, 1)];
        // rerank 给 d2 高分 → d2 应上升。
        let mut rerank = HashMap::new();
        rerank.insert("d2".into(), 0.99);
        rerank.insert("d1".into(), 0.1);
        let result = engine.fuse(bm25, dense, Some(&rerank), 10);
        assert_eq!(result[0].source_id, "d2");
        assert!(result[0].rerank_score.is_some());
    }

    #[test]
    fn freshness_decays_with_age() {
        let engine = test_engine();
        let old = chrono::Utc::now() - chrono::Duration::days(60);
        let bm25_old = vec![RetrievalCandidate {
            source_id: "old".into(),
            source_type: SourceType::OpenSearch,
            collection: "docs".into(),
            content: "old doc".into(),
            score: 0.9,
            rank: 1,
            content_hash: content_hash("old doc"),
            timestamp: Some(old),
            metadata: HashMap::new(),
        }];
        let bm25_new = vec![RetrievalCandidate {
            source_id: "new".into(),
            source_type: SourceType::OpenSearch,
            collection: "docs".into(),
            content: "new doc".into(),
            score: 0.9,
            rank: 1,
            content_hash: content_hash("new doc"),
            timestamp: Some(Utc::now()),
            metadata: HashMap::new(),
        }];
        let r_old = engine.fuse(bm25_old, vec![], None, 1);
        let r_new = engine.fuse(bm25_new, vec![], None, 1);
        // 新文档 freshness 更高 → 最终分数更高。
        assert!(r_new[0].score > r_old[0].score);
        assert!(r_old[0].freshness_score < r_new[0].freshness_score);
    }

    #[test]
    fn dynamic_weights_normalize_on_set() {
        let dw = DynamicWeights::new(1.0, 1.0, 1.0, 1.0);
        let s = dw.snapshot();
        let sum = s.bm25 + s.dense + s.rerank + s.freshness;
        assert!((sum - 1.0).abs() < 0.001, "sum should be 1.0, got {}", sum);
    }

    #[test]
    fn atomic_f64_roundtrip() {
        let a = AtomicF64::new(0.42);
        assert!((a.load(AtomicOrdering::Relaxed) - 0.42).abs() < 1e-9);
        a.store(0.99, AtomicOrdering::Relaxed);
        assert!((a.load(AtomicOrdering::Relaxed) - 0.99).abs() < 1e-9);
    }
}
