//! PatchMergeCore：顶层编排器，串联 diff / patch / merge 并维护运行时统计。
//!
//! ```text
//!   HTTP /diff ──→ diff::diff_result ──→ stats(diff)
//!   HTTP /patch ─→ patch::apply_patch ─→ stats(patch)
//!   HTTP /merge ─→ merge::three_way_merge → stats(merge)
//!       │
//!       ▼
//!   NATS agenthub.patch.merge.requested
//!       │  EventEnvelope { payload: MergeRequest }
//!       ▼
//!   three_way_merge → MergeResult
//!       │
//!       ▼
//!   NATS agenthub.patch.audit (patch.merge.completed)
//! ```
//!
//! 所有公开方法都做 `max_text_bytes` 防护，超限返回错误而非 OOM。

use std::sync::Arc;
use std::time::Instant;

use tokio::sync::Mutex;

use crate::diff::diff_result;
use crate::merge::three_way_merge;
use crate::patch::apply_patch;
use crate::types::{
    Change, ConflictRegion, DiffConfig, DiffOp, DiffResult, MergeRequest, MergeResult, Patch,
    PatchMergeStats, PatchResult,
};

/// patch-merge-core 顶层编排器。
pub struct PatchMergeCore {
    config: DiffConfig,
    stats: Arc<Mutex<PatchMergeStats>>,
}

impl PatchMergeCore {
    pub fn new(config: DiffConfig) -> Arc<Self> {
        Arc::new(Self {
            config,
            stats: Arc::new(Mutex::new(PatchMergeStats::default())),
        })
    }

    pub fn config(&self) -> &DiffConfig {
        &self.config
    }

    /// 计算 base → revised 的完整 diff 结果。
    pub async fn diff(&self, base: &str, revised: &str) -> Result<DiffResult, String> {
        self.guard_size(base, revised)?;
        let start = Instant::now();
        let result = diff_result(base, revised);
        let elapsed = start.elapsed().as_millis() as u64;
        self.record_diff(elapsed).await;
        Ok(result)
    }

    /// 在 base 上应用 patch。
    pub async fn apply_patch(&self, base: &str, patch: &Patch) -> Result<PatchResult, String> {
        self.guard_size(base, "")?;
        if patch.ops.len() > self.config.max_ops {
            return Err(format!(
                "patch ops {} exceeds max {}",
                patch.ops.len(),
                self.config.max_ops
            ));
        }
        let start = Instant::now();
        let result = apply_patch(base, patch);
        let elapsed = start.elapsed().as_millis() as u64;
        self.record_patch(elapsed, result.ok).await;
        Ok(result)
    }

    /// 三路合并。
    pub async fn merge(&self, req: &MergeRequest) -> Result<MergeResult, String> {
        self.guard_size(&req.base, &req.ours)?;
        self.guard_size(&req.theirs, "")?;
        let start = Instant::now();
        let outcome = three_way_merge(&req.base, &req.ours, &req.theirs);
        let elapsed = start.elapsed().as_millis() as u64;
        let has_conflicts = outcome.has_conflicts();
        let result = outcome.into_result();
        self.record_merge(elapsed, has_conflicts, result.conflicts.len())
            .await;
        Ok(result)
    }

    /// 当前统计快照。
    pub async fn stats(&self) -> PatchMergeStats {
        self.stats.lock().await.clone()
    }

    /// 健康状态：无下游依赖，永远 ready。
    pub fn health(&self) -> PatchMergeCoreHealth {
        PatchMergeCoreHealth { ready: true }
    }

    /// 文本大小防护，避免 OOM。
    fn guard_size(&self, a: &str, b: &str) -> Result<(), String> {
        let total = a.len() + b.len();
        if total > self.config.max_text_bytes {
            return Err(format!(
                "input {} bytes exceeds max {} bytes",
                total, self.config.max_text_bytes
            ));
        }
        Ok(())
    }

    async fn record_diff(&self, elapsed_ms: u64) {
        let mut s = self.stats.lock().await;
        s.diffs_total += 1;
        s.avg_diff_latency_ms = rolling_avg(s.avg_diff_latency_ms, s.diffs_total, elapsed_ms);
        *s.op_counts.entry("diff".into()).or_insert(0) += 1;
    }

    async fn record_patch(&self, elapsed_ms: u64, ok: bool) {
        let mut s = self.stats.lock().await;
        s.patches_total += 1;
        if !ok {
            s.patches_failed += 1;
        }
        s.avg_patch_latency_ms = rolling_avg(s.avg_patch_latency_ms, s.patches_total, elapsed_ms);
        *s.op_counts.entry("patch".into()).or_insert(0) += 1;
    }

    async fn record_merge(&self, elapsed_ms: u64, has_conflicts: bool, conflict_count: usize) {
        let mut s = self.stats.lock().await;
        s.merges_total += 1;
        if has_conflicts {
            s.merges_with_conflicts += 1;
            s.conflicts_total += conflict_count as u64;
        }
        s.avg_merge_latency_ms = rolling_avg(s.avg_merge_latency_ms, s.merges_total, elapsed_ms);
        *s.op_counts.entry("merge".into()).or_insert(0) += 1;
    }
}

/// 累积平均：`avg = (avg * (n-1) + x) / n`。
fn rolling_avg(prev: u64, n: u64, x: u64) -> u64 {
    if n == 0 {
        return 0;
    }
    ((prev as u128 * (n - 1) as u128 + x as u128) / n as u128) as u64
}

/// 健康状态快照。
#[derive(Debug, Clone, serde::Serialize)]
pub struct PatchMergeCoreHealth {
    pub ready: bool,
}

// 抑制未使用导入警告（Change/ConflictRegion/DiffOp 等在 HTTP 层与测试中使用）。
#[allow(dead_code)]
fn _type_use_markers(_c: &Change, _cr: &ConflictRegion, _op: &DiffOp) {}

#[cfg(test)]
mod tests {
    use super::*;

    fn core() -> Arc<PatchMergeCore> {
        PatchMergeCore::new(DiffConfig::default())
    }

    #[tokio::test]
    async fn diff_records_stats() {
        let c = core();
        let _ = c.diff("a\nb", "a\nB").await.unwrap();
        let s = c.stats().await;
        assert_eq!(s.diffs_total, 1);
        assert!(s.avg_diff_latency_ms >= 0);
    }

    #[tokio::test]
    async fn patch_records_success_and_failure() {
        let c = core();
        let base = "a\nb\nc";
        let patch = Patch {
            base_hash: crate::types::content_hash(base),
            ops: crate::diff::diff_lines(base, "a\nB\nc"),
        };
        let ok = c.apply_patch(base, &patch).await.unwrap();
        assert!(ok.ok);
        // 脏 base → 失败计数。
        let dirty = Patch {
            base_hash: "0000000000000000".into(),
            ops: vec![],
        };
        let fail = c.apply_patch(base, &dirty).await.unwrap();
        assert!(!fail.ok);

        let s = c.stats().await;
        assert_eq!(s.patches_total, 2);
        assert_eq!(s.patches_failed, 1);
    }

    #[tokio::test]
    async fn merge_records_conflicts() {
        let c = core();
        let req = MergeRequest {
            base: "a\nb\nc".into(),
            ours: "a\nB1\nc".into(),
            theirs: "a\nB2\nc".into(),
            trace_id: None,
        };
        let r = c.merge(&req).await.unwrap();
        assert!(r.has_conflicts);
        let s = c.stats().await;
        assert_eq!(s.merges_total, 1);
        assert_eq!(s.merges_with_conflicts, 1);
        assert_eq!(s.conflicts_total, 1);
    }

    #[tokio::test]
    async fn oversize_input_rejected() {
        let c = PatchMergeCore::new(DiffConfig {
            max_text_bytes: 8,
            max_ops: 100,
        });
        let big = "0123456789abcdef";
        let err = c.diff(big, big).await.err();
        assert!(err.is_some());
    }

    #[tokio::test]
    async fn health_always_ready() {
        let c = core();
        assert!(c.health().ready);
    }

    #[tokio::test]
    async fn rolling_avg_behaves() {
        assert_eq!(rolling_avg(0, 1, 10), 10);
        assert_eq!(rolling_avg(10, 2, 20), 15);
        assert_eq!(rolling_avg(0, 0, 5), 0);
    }
}
