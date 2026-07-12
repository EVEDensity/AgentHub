//! OpenSearch BM25 稀疏检索客户端。
//!
//! 通过 OpenSearch REST API `/{index}/_search` 执行全文检索。
//! 每个 knowledge_scope 项映射到索引名：`knowledge-{scope}`（如 `knowledge-docs`）。
//! BM25 是 OpenSearch 的默认打分算法，无需额外配置。

use std::time::Duration;

use reqwest::Client;
use serde::{Deserialize, Serialize};

use crate::types::{content_hash, RetrievalCandidate, SourceType};

/// OpenSearch 搜索请求体。
#[derive(Debug, Serialize)]
struct SearchBody {
    query: MatchQuery,
    size: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    sort: Option<Vec<serde_json::Value>>,
}

#[derive(Debug, Serialize)]
struct MatchBody {
    #[serde(rename = "content")]
    content: String,
}

#[derive(Debug, Serialize)]
struct MatchQuery {
    #[serde(rename = "match")]
    r#match: MatchBody,
}

/// OpenSearch 搜索响应。
#[derive(Debug, Deserialize)]
struct SearchResponse {
    hits: HitsWrapper,
}

#[derive(Debug, Deserialize)]
struct HitsWrapper {
    hits: Vec<EsHit>,
}

#[derive(Debug, Deserialize)]
struct EsHit {
    #[serde(rename = "_id")]
    id: String,
    #[serde(rename = "_score")]
    score: f64,
    #[serde(rename = "_source")]
    source: serde_json::Value,
}

/// OpenSearch 客户端。
pub struct OpenSearchClient {
    client: Client,
    base_url: String,
}

impl OpenSearchClient {
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

    /// 在指定索引中执行 BM25 全文搜索。
    ///
    /// `tenant_id` 预留给后续租户级过滤（OpenSearch term query），当前实现暂未使用。
    pub async fn search(
        &self,
        index: &str,
        query: &str,
        limit: usize,
        _tenant_id: &str,
    ) -> Result<Vec<RetrievalCandidate>, OpenSearchError> {
        let url = format!("{}/{}/_search", self.base_url, index);
        let body = SearchBody {
            query: MatchQuery {
                r#match: MatchBody {
                    content: query.to_string(),
                },
            },
            size: limit,
            sort: None,
        };

        let resp = self.client.post(&url).json(&body).send().await?;
        if !resp.status().is_success() {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            return Err(OpenSearchError::Status(status, text));
        }
        let search_resp: SearchResponse = resp.json().await?;

        // 归一化 BM25 分数到 [0,1]：用 max-score 归一化。
        let max_score = search_resp
            .hits
            .hits
            .iter()
            .map(|h| h.score)
            .fold(0.0_f64, f64::max)
            .max(1.0);

        let candidates = search_resp
            .hits
            .hits
            .into_iter()
            .enumerate()
            .map(|(i, hit)| {
                let source = hit.source;
                let content = source
                    .get("content")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let source_id = source
                    .get("source_id")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string())
                    .unwrap_or_else(|| format!("os-{}", hit.id));
                let timestamp = source
                    .get("timestamp")
                    .and_then(|v| v.as_str())
                    .and_then(|s| chrono::DateTime::parse_from_rfc3339(s).ok())
                    .map(|dt| dt.with_timezone(&chrono::Utc));
                let normalized = (hit.score / max_score) as f32;
                // 先算哈希再 move content，避免 borrow-after-move。
                let hash = content_hash(&content);
                RetrievalCandidate {
                    source_id,
                    source_type: SourceType::OpenSearch,
                    collection: index.to_string(),
                    content,
                    score: normalized,
                    rank: i + 1,
                    content_hash: hash,
                    timestamp,
                    metadata: flatten_metadata(&source),
                }
            })
            .collect();

        Ok(candidates)
    }

    /// 健康检查：GET /_cluster/health。
    pub async fn health(&self) -> Result<bool, OpenSearchError> {
        let url = format!("{}/_cluster/health", self.base_url);
        let resp = self.client.get(&url).send().await?;
        Ok(resp.status().is_success())
    }
}

fn flatten_metadata(source: &serde_json::Value) -> std::collections::HashMap<String, serde_json::Value> {
    let mut map = std::collections::HashMap::new();
    if let Some(obj) = source.as_object() {
        for (k, v) in obj {
            if k != "content" && k != "source_id" && k != "timestamp" {
                map.insert(k.clone(), v.clone());
            }
        }
    }
    map
}

#[derive(Debug, thiserror::Error)]
pub enum OpenSearchError {
    #[error("http transport: {0}")]
    Transport(#[from] reqwest::Error),
    #[error("opensearch returned {0}: {1}")]
    Status(reqwest::StatusCode, String),
}
