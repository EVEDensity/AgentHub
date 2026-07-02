//! # patch-merge-core
//!
//! AgentHub 平台的产物 diff/patch/merge 引擎，提供行级 diff、patch 应用、
//! 三路合并与冲突打分能力：
//!
//! 1. **行级 diff**（[`diff`]）：基于 LCS（最长公共子序列）的 DP 算法，
//!    把 base 与 revised 的文本差异表示为 `Equal/Delete/Insert` 操作序列
//!    （[`DiffOp`]），并可聚合成 [`Change`] 区段（base 区间 → 替换行）。
//! 2. **patch 应用**（[`patch`]）：根据 [`Patch`]（base 内容哈希 + 操作序列）
//!    在原 base 上重建修订文本；base_hash 不匹配则拒绝，避免在脏 base 上误用。
//! 3. **三路合并**（[`merge`]）：以 base 为基准，分别计算 ours/theirs 的
//!    [`Change`] 列表，按 base 位置游走——不重叠则双取，重叠且相同则取一次，
//!    重叠且不同则产生 Git 风格冲突标记（`<<<<<<< / ======= / >>>>>>>`）并
//!    计入 [`ConflictRegion`]；冲突率 = 冲突行 / 总变更行。
//!
//! 接入方式：通过 NATS 订阅 `agenthub.patch.merge.requested`，处理后发布
//! `patch.merge.completed` 到 `agenthub.patch.audit`。详见 [`nats::NatsAdapter`]。
//!
//! 降级链：
//! - base_hash 不匹配 → 拒绝 patch，返回错误而非猜测。
//! - NATS 不可用 → 降级为 HTTP-only 模式（仍可同步 /diff /patch /merge），不消费事件。
//!
//! 启动顺序：HTTP 服务先 `tokio::spawn`，NATS 连接留主 future——确保 K8s
//! liveness 探针在 NATS 重试期间也能拿到 `/healthz`。

pub mod core;
pub mod diff;
pub mod merge;
pub mod nats;
pub mod patch;
pub mod server;
pub mod types;

// ── 顶层 re-export（常用类型直接可 `use patch_merge_core::X`）────────
pub use core::{PatchMergeCore, PatchMergeCoreHealth};
pub use diff::{diff_lines, diff_to_changes, lcs_matching_blocks};
pub use merge::{three_way_merge, MergeOutcome};
pub use patch::apply_patch;
pub use types::{
    Change, ConflictRegion, DiffConfig, DiffOp, DiffResult, MergeRequest, MergeResult, Patch,
    PatchMergeStats, PatchResult,
};

/// Crate 版本（与 Cargo workspace 一致）。
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// 保留原桩函数签名，避免破坏既有调用点（仅作能力探测）。
pub fn supports_three_way_merge() -> bool {
    true
}
