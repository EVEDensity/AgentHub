//! model-adapter 客户端：embedding（查询向量化）+ rerank（重排）。
//!
//! 两个端点：
//! - `POST /v1/embeddings`：把查询文本转为向量（已实现）。
//! - `POST /v1/rerank`：对候选列表重排（**尚未在 model-adapter 中实现**，会返回 404，
//!   调用方应捕获错误并降级为 "fusion score only"）。
//!
//! 所有调用都带超时；超时或错误时返回 `Err`，由调用方决定降级策略。

use std::time::Duration;

use reqwest::Client;
use serde::{Deserialize, Serialize};

// ── Embedding ──────────────────────────────────────────────────────────

#[derive(Debug, Serialize)]
struct EmbeddingRequest {
    model: String,
    input: String,
}

#[derive(Debug, Deserialize)]
struct EmbeddingResponse {
    data: Vec<EmbeddingData>,
}

#[derive(Debug, Deserialize)]
struct EmbeddingData {
    embedding: Vec<f32>,
}

// ── Rerank ─────────────────────────────────────────────────────────────

#[derive(Debug, Serialize)]
struct RerankRequest {
    query: String,
    documents: Vec<String>,
    top_n: usize,
}

#[derive(Debug, Deserialize)]
struct RerankResponse {
    data: Vec<RerankResult>,
}

#[derive(Debug, Deserialize)]
struct RerankResult {
    index: usize,
    relevance_score: f64,
}

/// model-adapter 客户端。
pub struct ModelAdapterClient {
    client: Client,
    base_url: String,
    embedding_model: String,
}

impl ModelAdapterClient {
    pub fn new(url: &str, embedding_model: &str, timeout: Duration) -> Self {
        let client = Client::builder()
            .timeout(timeout)
            .build()
            .expect("build reqwest client");
        Self {
            client,
            base_url: url.trim_end_matches('/').to_string(),
            embedding_model: embedding_model.to_string(),
        }
    }

    /// 把查询文本转为向量。
    pub async fn embed(&self, query: &str) -> Result<Vec<f32>, ModelAdapterError> {
        let url = format!("{}/v1/embeddings", self.base_url);
        let body = EmbeddingRequest {
            model: self.embedding_model.clone(),
            input: query.to_string(),
        };
        let resp = self.client.post(&url).json(&body).send().await?;
        if !resp.status().is_success() {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            return Err(ModelAdapterError::Status(status, text));
        }
        let emb_resp: EmbeddingResponse = resp.json().await?;
        emb_resp
            .data
            .into_iter()
            .next()
            .map(|d| d.embedding)
            .ok_or(ModelAdapterError::EmptyResponse)
    }

    /// 对候选列表重排。返回 `Vec<(document_index, rerank_score)>`，
    /// index 对应传入 `documents` 的位置。
    ///
    /// **注意**：model-adapter 目前可能未实现此端点（返回 404），
    /// 调用方应捕获错误并降级。
    pub async fn rerank(
        &self,
        query: &str,
        documents: &[String],
        top_n: usize,
    ) -> Result<Vec<(usize, f32)>, ModelAdapterError> {
        let url = format!("{}/v1/rerank", self.base_url);
        let body = RerankRequest {
            query: query.to_string(),
            documents: documents.to_vec(),
            top_n,
        };
        let resp = self.client.post(&url).json(&body).send().await?;
        if !resp.status().is_success() {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            return Err(ModelAdapterError::Status(status, text));
        }
        let rr: RerankResponse = resp.json().await?;
        Ok(rr.data.into_iter().map(|r| (r.index, r.relevance_score as f32)).collect())
    }

    /// 健康检查。
    pub async fn health(&self) -> Result<bool, ModelAdapterError> {
        let url = format!("{}/healthz", self.base_url);
        let resp = self.client.get(&url).send().await?;
        Ok(resp.status().is_success())
    }
}

#[derive(Debug, thiserror::Error)]
pub enum ModelAdapterError {
    #[error("http transport: {0}")]
    Transport(#[from] reqwest::Error),
    #[error("model-adapter returned {0}: {1}")]
    Status(reqwest::StatusCode, String),
    #[error("empty embedding response")]
    EmptyResponse,
}
