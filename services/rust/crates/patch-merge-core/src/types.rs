//! patch-merge-core 数据模型。
//!
//! 一个 [`DiffOp`] 描述 base↔revised 的一行差异；多个相邻 Delete/Insert 聚合成
//! [`Change`]（base 区间 → 替换行）。[`Patch`] = base_hash + 操作序列，用于
//! 离线传递增量。三路合并以 [`Change`] 为单位游走 ours/theirs，产生
//! [`MergeResult`]（含 Git 风格冲突标记）。
//!
//! 数据流：
//! ```text
//!   base ──┬── diff_to_changes ──→ changes_ours
//!          │
//!          └── diff_to_changes ──→ changes_theirs
//!                                     │
//!                                     ▼
//!                              ┌──────────────┐
//!                              │ three_way_   │  不重叠→双取
//!                              │ merge        │  重叠相同→取一次
//!                              │              │  重叠不同→冲突标记
//!                              └──────┬───────┘
//!                                     │
//!                                     ▼
//!                          MergeResult { merged_text, conflicts, conflict_score }
//! ```

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// diff/merge 配置。
#[derive(Debug, Clone)]
pub struct DiffConfig {
    /// 单次处理的文本上限（字节），超过则拒绝，防止 OOM。
    pub max_text_bytes: usize,
    /// patch 应用失败前的最大操作数（防御性）。
    pub max_ops: usize,
}

impl Default for DiffConfig {
    fn default() -> Self {
        Self {
            max_text_bytes: 8 * 1024 * 1024, // 8 MiB
            max_ops: 200_000,
        }
    }
}

/// 单行 diff 操作。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "op", content = "line", rename_all = "snake_case")]
pub enum DiffOp {
    /// 两端都有的相同行（context）。
    Equal(String),
    /// 仅 base 有（被删除）。
    Delete(String),
    /// 仅 revised 有（被插入）。
    Insert(String),
}

impl DiffOp {
    /// 该操作是否消耗 base 的一行。
    pub fn consumes_base(&self) -> bool {
        matches!(self, DiffOp::Equal(_) | DiffOp::Delete(_))
    }

    /// 该操作是否产生输出行。
    pub fn emits(&self) -> bool {
        matches!(self, DiffOp::Equal(_) | DiffOp::Insert(_))
    }

    pub fn as_str(&self) -> &'static str {
        match self {
            DiffOp::Equal(_) => "equal",
            DiffOp::Delete(_) => "delete",
            DiffOp::Insert(_) => "insert",
        }
    }
}

/// diff 结果：操作序列 + 统计。
#[derive(Debug, Clone, Serialize)]
pub struct DiffResult {
    pub ops: Vec<DiffOp>,
    pub base_lines: usize,
    pub revised_lines: usize,
    pub equal_lines: usize,
    pub deleted_lines: usize,
    pub inserted_lines: usize,
    /// base 内容哈希（FNV-1a 64-bit hex）。
    pub base_hash: String,
    /// revised 内容哈希。
    pub revised_hash: String,
}

/// 一个变更区段：用 `replacement` 替换 base 的 `[base_start, base_end)` 行。
/// `base_end == base_start` 表示纯插入。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Change {
    /// base 起始行（含）。
    pub base_start: usize,
    /// base 结束行（不含）。
    pub base_end: usize,
    /// 替换内容（逐行）。
    pub replacement: Vec<String>,
}

impl Change {
    /// 是否与另一变更在 base 上重叠。
    pub fn overlaps(&self, other: &Change) -> bool {
        self.base_start < other.base_end && other.base_start < self.base_end
    }

    /// 是否为纯删除（replacement 为空）。
    pub fn is_pure_delete(&self) -> bool {
        self.base_end > self.base_start && self.replacement.is_empty()
    }

    /// 是否为纯插入（base 区间为空）。
    pub fn is_pure_insert(&self) -> bool {
        self.base_end == self.base_start && !self.replacement.is_empty()
    }
}

/// Patch：可序列化的增量包，用于离线传递或在脏 base 上校验。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Patch {
    /// base 内容哈希，应用前校验，避免在错误 base 上误用。
    pub base_hash: String,
    pub ops: Vec<DiffOp>,
}

/// patch 应用结果。
#[derive(Debug, Clone, Serialize)]
pub struct PatchResult {
    pub ok: bool,
    pub result_text: String,
    pub applied_ops: usize,
    pub error: Option<String>,
}

/// 三路合并请求。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MergeRequest {
    pub base: String,
    pub ours: String,
    pub theirs: String,
    /// 可选：发起方 trace 透传。
    #[serde(default)]
    pub trace_id: Option<String>,
}

/// 冲突区段：base 行区间 + 双方各自替换内容。
#[derive(Debug, Clone, Serialize)]
pub struct ConflictRegion {
    pub base_start: usize,
    pub base_end: usize,
    pub ours: Vec<String>,
    pub theirs: Vec<String>,
}

/// 三路合并结果。
#[derive(Debug, Clone, Serialize)]
pub struct MergeResult {
    /// 合并后的文本（含冲突标记）。
    pub merged_text: String,
    /// 冲突区段列表（可能为空）。
    pub conflicts: Vec<ConflictRegion>,
    /// 冲突率：冲突行数 / 总变更行数，0.0=无冲突，1.0=全冲突。
    pub conflict_score: f64,
    pub base_lines: usize,
    pub ours_lines: usize,
    pub theirs_lines: usize,
    pub merged_lines: usize,
    /// 是否存在冲突。
    pub has_conflicts: bool,
}

/// 运行时统计。
#[derive(Debug, Default, Clone, Serialize)]
pub struct PatchMergeStats {
    pub diffs_total: u64,
    pub patches_total: u64,
    pub patches_failed: u64,
    pub merges_total: u64,
    pub merges_with_conflicts: u64,
    pub conflicts_total: u64,
    pub avg_diff_latency_ms: u64,
    pub avg_patch_latency_ms: u64,
    pub avg_merge_latency_ms: u64,
    /// 各操作的最近计数（监控用）。
    pub op_counts: HashMap<String, u64>,
}

/// FNV-1a 64-bit 内容哈希（十六进制小写）。
/// 不引入额外依赖，与 fanout-core 的分区哈希算法一致；仅用于 base 完整性
/// 校验，非密码学用途。
pub fn content_hash(text: &str) -> String {
    let mut hash: u64 = 0xcbf29ce484222325;
    for &b in text.as_bytes() {
        hash ^= b as u64;
        hash = hash.wrapping_mul(0x100000001b3);
    }
    format!("{:016x}", hash)
}

/// 按行切分文本（保留尾部空行语义：`"a\n"` → `["a", ""]`，与 `join("\n")` 互逆）。
pub fn split_lines(text: &str) -> Vec<String> {
    text.split('\n').map(String::from).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn content_hash_is_deterministic() {
        assert_eq!(content_hash("hello"), content_hash("hello"));
        assert_ne!(content_hash("hello"), content_hash("world"));
        // 空串有确定哈希。
        assert_eq!(content_hash("").len(), 16);
    }

    #[test]
    fn split_join_roundtrips() {
        for s in ["", "a", "a\n", "a\nb", "a\nb\n", "\n"] {
            let lines = split_lines(s);
            assert_eq!(lines.join("\n"), s, "roundtrip failed for {:?}", s);
        }
    }

    #[test]
    fn change_overlap_detection() {
        let a = Change { base_start: 0, base_end: 3, replacement: vec![] };
        let b = Change { base_start: 2, base_end: 5, replacement: vec![] };
        assert!(a.overlaps(&b));
        let c = Change { base_start: 3, base_end: 5, replacement: vec![] };
        assert!(!a.overlaps(&c)); // 相邻不重叠
    }

    #[test]
    fn diff_op_helpers() {
        assert!(DiffOp::Equal("x".into()).consumes_base());
        assert!(DiffOp::Delete("x".into()).consumes_base());
        assert!(!DiffOp::Insert("x".into()).consumes_base());
        assert!(DiffOp::Equal("x".into()).emits());
        assert!(!DiffOp::Delete("x".into()).emits());
        assert!(DiffOp::Insert("x".into()).emits());
    }
}
