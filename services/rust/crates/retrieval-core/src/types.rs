//! 检索核心数据模型。
//!
//! 一个 [`RetrievalRequest`] 来自 NATS 事件 `retrieval.query.requested`，
//! 经过 hybrid retrieve → fusion → rerank → citation shaping 后，
//! 产出 [`FusionResult`]（发布 `retrieval.fusion.completed`）。
//!
//! 数据流：
//! ```text
//!   retrieval.query.requested (NATS)
//!        │
//!        ▼
//!   ┌──────────────┐   query text    ┌──────────────┐
//!   │ Embedding    │ ──────────────▶ │ model-adapter │
//!   │ (model-adapter)│ ◀──────────── │ /v1/embeddings│
//!   └──────┬───────┘   vector        └──────────────┘
//!          │
//!          ├──▶ Qdrant (dense)  ──▶ RetrievalCandidate[]
//!          │
//!          └──▶ OpenSearch (BM25) ──▶ RetrievalCandidate[]
//!                                       │
//!                                       ▼
//!                                 ┌──────────┐
//!                                 │  Fusion  │ (RRF + freshness + dedup)
//!                                 └────┬─────┘
//!                                      │
//!                                      ▼
//!                                 ┌──────────┐
//!                                 │  Rerank  │ (model-adapter, 可降级)
//!                                 └────┬─────┘
//!                                      │
//!                                      ▼
//!                              FusedCandidate[] → Citation[]
//! ```

use std::collections::HashMap;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// 检索来源类型。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SourceType {
    /// Qdrant 稠密向量检索。
    Qdrant,
    /// OpenSearch BM25 稀疏检索。
    OpenSearch,
}

/// 降级标记：记录哪些 fallback 被触发。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Degradation {
    /// BM25 超时，仅用 dense。
    DenseOnly,
    /// Rerank 超时/不可用，仅用融合分数。
    FusionScoreOnly,
    /// Dense 超时，仅用 BM25。
    Bm25Only,
}

impl Degradation {
    pub fn as_str(&self) -> &'static str {
        match self {
            Degradation::DenseOnly => "dense_only",
            Degradation::FusionScoreOnly => "fusion_score_only",
            Degradation::Bm25Only => "bm25_only",
        }
    }
}

/// 检索请求（从 `retrieval.query.requested` 事件 payload 解析）。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RetrievalRequest {
    pub request_id: String,
    /// 原始查询文本（已由 search-agent 改写/分解后）。
    pub query: String,
    /// 检索模式：`deepsearch` | `simple`。
    #[serde(default = "default_mode")]
    pub mode: String,
    /// 知识范围：`["docs", "code", "memory"]`。
    #[serde(default = "default_knowledge_scope")]
    pub knowledge_scope: Vec<String>,
    /// 租户 ID（从 envelope 顶层取，非 payload）。
    #[serde(skip)]
    pub tenant_id: String,
    /// 会话 ID（从 envelope 顶层取）。
    #[serde(skip)]
    pub session_id: String,
    /// trace ID（从 envelope 顶层取）。
    #[serde(skip)]
    pub trace_id: String,
    /// 返回 top-k 结果数。
    #[serde(default = "default_top_k")]
    pub top_k: usize,
    /// 检索超时预算（毫秒）。
    #[serde(default = "default_timeout_ms")]
    pub timeout_ms: u64,
    /// 额外过滤条件。
    #[serde(default)]
    pub filters: HashMap<String, serde_json::Value>,
}

fn default_mode() -> String {
    "deepsearch".into()
}
fn default_knowledge_scope() -> Vec<String> {
    vec!["docs".into(), "code".into(), "memory".into()]
}
fn default_top_k() -> usize {
    8
}
fn default_timeout_ms() -> u64 {
    2000
}

/// 单条检索候选（来自 Qdrant 或 OpenSearch）。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RetrievalCandidate {
    /// 文档/分片唯一 ID。
    pub source_id: String,
    /// 来源类型。
    pub source_type: SourceType,
    /// 所属集合 / 索引名（如 "docs" / "knowledge-docs"）。
    pub collection: String,
    /// 文本内容片段。
    pub content: String,
    /// 原始分数（已归一化到 [0,1]）。
    pub score: f32,
    /// 在来源结果中的排名（1-indexed）。
    pub rank: usize,
    /// 内容哈希（用于跨源去重）。
    pub content_hash: String,
    /// 文档时间戳（用于 freshness 打分；缺失则视为很久以前）。
    pub timestamp: Option<DateTime<Utc>>,
    /// 附加元数据。
    #[serde(default)]
    pub metadata: HashMap<String, serde_json::Value>,
}

/// 融合后的候选：携带各源分数与最终分数。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FusedCandidate {
    pub source_id: String,
    /// 最终融合分数（[0,1]）。
    pub score: f32,
    pub content: String,
    pub collection: String,
    /// BM25 归一化分数（若该候选来自 BM25）。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bm25_score: Option<f32>,
    /// Dense 归一化分数（若该候选来自 dense）。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub dense_score: Option<f32>,
    /// Rerank 分数（若 rerank 可用）。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rerank_score: Option<f32>,
    /// Freshness 衰减系数 [0,1]。
    pub freshness_score: f32,
    #[serde(default)]
    pub metadata: HashMap<String, serde_json::Value>,
}

/// 引用条目：融合结果的 citation-ready 视图。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Citation {
    pub source_id: String,
    pub score: f32,
    pub collection: String,
    /// 内容摘要（截断到 ~200 字符）。
    pub snippet: String,
}

/// 融合结果：发布为 `retrieval.fusion.completed`。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FusionResult {
    pub request_id: String,
    /// 融合策略名（固定 `bm25_dense_rerank_freshness`）。
    pub strategy: String,
    pub top_k: usize,
    pub citations: Vec<Citation>,
    /// 完整候选（含各源分数，供 debug / result_ref 序列化）。
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub candidates: Vec<FusedCandidate>,
    pub qdrant_hits: usize,
    pub opensearch_hits: usize,
    pub elapsed_ms: u64,
    /// 触发的降级标记。
    #[serde(default)]
    pub degraded: Vec<String>,
}

/// 查询完成事件：发布为 `retrieval.query.completed`。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct QueryCompleted {
    pub request_id: String,
    pub candidate_count: usize,
    pub qdrant_hits: usize,
    pub opensearch_hits: usize,
    pub elapsed_ms: u64,
    /// MinIO 结果引用（若配置了上传）。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result_ref: Option<String>,
}

impl FusionResult {
    /// 从融合候选构建 citation 列表（截断 content 为 snippet）。
    pub fn citations_from(candidates: &[FusedCandidate]) -> Vec<Citation> {
        candidates
            .iter()
            .map(|c| Citation {
                source_id: c.source_id.clone(),
                score: c.score,
                collection: c.collection.clone(),
                snippet: truncate(&c.content, 200),
            })
            .collect()
    }
}

fn truncate(s: &str, max: usize) -> String {
    if s.len() <= max {
        s.to_string()
    } else {
        let mut end = max;
        while !s.is_char_boundary(end) {
            end -= 1;
        }
        format!("{}…", &s[..end])
    }
}

/// 计算内容的简易哈希（用于去重）。
/// 用 FNV-1a 而非 sha2，避免引入额外依赖；去重精度足够。
pub fn content_hash(s: &str) -> String {
    let mut hash: u64 = 0xcbf29ce484222325;
    for b in s.as_bytes() {
        hash ^= *b as u64;
        hash = hash.wrapping_mul(0x100000001b3);
    }
    format!("{:016x}", hash)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn content_hash_is_deterministic() {
        let h1 = content_hash("hello world");
        let h2 = content_hash("hello world");
        let h3 = content_hash("hello earth");
        assert_eq!(h1, h2);
        assert_ne!(h1, h3);
    }

    #[test]
    fn truncate_respects_char_boundary() {
        let s = "你好世界你好世界你好世界"; // 每字 3 bytes UTF-8
        let t = truncate(s, 7);
        assert!(t.ends_with('…'));
        // 不应在多字节字符中间截断
        assert!(t.chars().count() < s.chars().count());
    }

    #[test]
    fn request_deserializes_with_defaults() {
        let json = r#"{"request_id":"r1","query":"test"}"#;
        let req: RetrievalRequest = serde_json::from_str(json).unwrap();
        assert_eq!(req.mode, "deepsearch");
        assert_eq!(req.top_k, 8);
        assert_eq!(req.timeout_ms, 2000);
        assert_eq!(req.knowledge_scope.len(), 3);
    }
}
