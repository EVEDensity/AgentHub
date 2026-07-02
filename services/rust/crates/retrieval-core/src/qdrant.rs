//! Qdrant 稠密向量检索客户端。
//!
//! 通过 Qdrant REST API `/collections/{collection}/points/search` 检索。
//! 每个 knowledge_scope 项对应一个 Qdrant collection（docs/code/memory/artifacts）。
//! 查询向量由 model-adapter 的 `/v1/embeddings` 生成。

use std::time::Duration;

use reqwest::Client;
use serde::{Deserialize, Serialize};

use crate::types::{content_hash, RetrievalCandidate, SourceType};

/// Qdrant 搜索请求体。
#[derive(Debug, Serialize)]
struct SearchRequest {
    vector: Vec<f32>,
    limit: usize,
    with_payload: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    score_threshold: Option<f32>,
}

/// Qdrant 搜索响应。
#[derive(Debug, Deserialize)]
struct SearchResponse {
    result: Vec<SearchHit>,
}

#[derive(Debug, Deserialize)]
struct SearchHit {
    id: serde_json::Value,
    score: f64,
    payload: serde_json::Value,
}

/// Qdrant 客户端。
pub struct QdrantClient {
    client: Client,
    base_url: String,
}

impl QdrantClient {
    pub fn new(url: &str, timeout: Duration) -> Self {
        let client = Client::builder()
            .timeout(timeout)
            .build()
            .expect("build reqwest client");
        Self {
            client,
            base_url: url.trim_end_matches('/').to_string(),
        }
    }

    /// 在指定 collection 中用向量搜索，返回候选列表。
    ///
    /// `tenant_id` 预留给后续租户级过滤（Qdrant filter），当前实现暂未使用。
    pub async fn search(
        &self,
        collection: &str,
        vector: &[f32],
        limit: usize,
        _tenant_id: &str,
    ) -> Result<Vec<RetrievalCandidate>, QdrantError> {
        let url = format!(
            "{}/collections/{}/points/search",
            self.base_url, collection
        );
        let body = SearchRequest {
            vector: vector.to_vec(),
            limit,
            with_payload: true,
            score_threshold: None,
        };

        let resp = self.client.post(&url).json(&body).send().await?;
        if !resp.status().is_success() {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            return Err(QdrantError::Status(status, text));
        }
        let search_resp: SearchResponse = resp.json().await?;

        // 归一化分数到 [0,1]：Qdrant cosine similarity 通常在 [-1,1]，截断到 [0,1]。
        let candidates = search_resp
            .result
            .into_iter()
            .enumerate()
            .map(|(i, hit)| {
                let payload = hit.payload;
                let content = payload
                    .get("content")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let source_id = payload
                    .get("source_id")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string())
                    .unwrap_or_else(|| format!("qdrant-{}", hit.id));
                let timestamp = payload
                    .get("timestamp")
                    .and_then(|v| v.as_str())
                    .and_then(|s| chrono::DateTime::parse_from_rfc3339(s).ok())
                    .map(|dt| dt.with_timezone(&chrono::Utc));
                let score = (hit.score as f32).max(0.0).min(1.0);
                // 先算哈希再 move content，避免 borrow-after-move。
                let hash = content_hash(&content);
                RetrievalCandidate {
                    source_id,
                    source_type: SourceType::Qdrant,
                    collection: collection.to_string(),
                    content,
                    score,
                    rank: i + 1,
                    content_hash: hash,
                    timestamp,
                    metadata: flatten_metadata(&payload),
                }
            })
            .collect();

        Ok(candidates)
    }

    /// 健康检查：GET /healthz。
    pub async fn health(&self) -> Result<bool, QdrantError> {
        let url = format!("{}/healthz", self.base_url);
        let resp = self.client.get(&url).send().await?;
        Ok(resp.status().is_success())
    }
}

/// 把 JSON payload 扁平化为 metadata map（顶层 key → value）。
fn flatten_metadata(payload: &serde_json::Value) -> std::collections::HashMap<String, serde_json::Value> {
    let mut map = std::collections::HashMap::new();
    if let Some(obj) = payload.as_object() {
        for (k, v) in obj {
            if k != "content" && k != "source_id" && k != "timestamp" {
                map.insert(k.clone(), v.clone());
            }
        }
    }
    map
}

#[derive(Debug, thiserror::Error)]
pub enum QdrantError {
    #[error("http transport: {0}")]
    Transport(#[from] reqwest::Error),
    #[error("qdrant returned {0}: {1}")]
    Status(reqwest::StatusCode, String),
}
