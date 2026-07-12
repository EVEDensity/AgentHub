//! 三路合并（diff3 风格）。
//!
//! 算法：
//! 1. 快速通道：ours==theirs → 取任一；ours==base → 取 theirs；theirs==base → 取 ours。
//! 2. 分别计算 `changes_ours = diff_to_changes(base, ours)` 与
//!    `changes_theirs = diff_to_changes(base, theirs)`。
//! 3. 按 base 位置游走两个 change 列表：
//!    - 不重叠（一方的 base_end ≤ 另一方 base_start）→ 先到先应用，互不干扰。
//!    - 重叠且 replacement 相同 → 视为同一变更，应用一次。
//!    - 重叠且 replacement 不同 → 产生 Git 风格冲突标记
//!      （`<<<<<<< ours / ======= / >>>>>>> theirs`）并计入 [`ConflictRegion`]。
//! 4. 冲突率 = 冲突涉及行数 / 总变更行数。
//!
//! 局限（v1）：重叠区采用成对消耗（advance both oi/ti），对"一个 change 跨越
//! 多个对方 change"的级联重叠可能不完整；典型编辑场景已足够，复杂级联留待
//! 后续按 diff3 的 matching-block 算法迭代。

use crate::diff::diff_to_changes_slices;
use crate::types::{split_lines, Change, ConflictRegion, MergeRequest, MergeResult};

/// 合并结果分类：无冲突（Clean）或含冲突（Conflicted，`merged_text` 含标记）。
#[derive(Debug, Clone)]
pub enum MergeOutcome {
    Clean(MergeResult),
    Conflicted(MergeResult),
}

impl MergeOutcome {
    /// 取内部 [`MergeResult`]。
    pub fn into_result(self) -> MergeResult {
        match self {
            MergeOutcome::Clean(r) | MergeOutcome::Conflicted(r) => r,
        }
    }

    /// 是否含冲突。
    pub fn has_conflicts(&self) -> bool {
        matches!(self, MergeOutcome::Conflicted(_))
    }
}

/// 执行三路合并。
pub fn three_way_merge(base: &str, ours: &str, theirs: &str) -> MergeOutcome {
    // 1. 快速通道。
    if ours == theirs {
        return clean(base, ours, theirs, ours);
    }
    if ours == base {
        return clean(base, ours, theirs, theirs);
    }
    if theirs == base {
        return clean(base, ours, theirs, ours);
    }

    let base_lines = split_lines(base);
    let our_lines = split_lines(ours);
    let their_lines = split_lines(theirs);

    let our_changes = diff_to_changes_slices(&base_lines, &our_lines);
    let their_changes = diff_to_changes_slices(&base_lines, &their_lines);

    let mut merged: Vec<String> = Vec::new();
    let mut conflicts: Vec<ConflictRegion> = Vec::new();
    let mut base_idx = 0usize;
    let mut total_changed_lines = 0usize;
    let mut conflict_lines = 0usize;
    let mut oi = 0usize;
    let mut ti = 0usize;

    while oi < our_changes.len() || ti < their_changes.len() {
        match (our_changes.get(oi), their_changes.get(ti)) {
            (Some(o), Some(t)) => {
                if o.base_end <= t.base_start {
                    // ours 在 theirs 之前，不重叠。
                    copy_base(&base_lines, &mut merged, &mut base_idx, o.base_start);
                    apply_change(&mut merged, o);
                    total_changed_lines += change_span(o);
                    base_idx = o.base_end;
                    oi += 1;
                } else if t.base_end <= o.base_start {
                    // theirs 在 ours 之前，不重叠。
                    copy_base(&base_lines, &mut merged, &mut base_idx, t.base_start);
                    apply_change(&mut merged, t);
                    total_changed_lines += change_span(t);
                    base_idx = t.base_end;
                    ti += 1;
                } else {
                    // 重叠区。
                    let start = o.base_start.min(t.base_start);
                    let end = o.base_end.max(t.base_end);
                    copy_base(&base_lines, &mut merged, &mut base_idx, start);
                    if o.replacement == t.replacement {
                        // 同一变更，应用一次。
                        merged.extend(o.replacement.iter().cloned());
                        total_changed_lines += change_span_region(start, end, &o.replacement);
                    } else {
                        // 冲突：Git 风格标记。
                        merged.push("<<<<<<< ours".into());
                        merged.extend(o.replacement.iter().cloned());
                        merged.push("=======".into());
                        merged.extend(t.replacement.iter().cloned());
                        merged.push(">>>>>>> theirs".into());
                        let span = change_span_region(start, end, &o.replacement)
                            .max(change_span_region(start, end, &t.replacement));
                        conflict_lines += span;
                        total_changed_lines += span;
                        conflicts.push(ConflictRegion {
                            base_start: start,
                            base_end: end,
                            ours: o.replacement.clone(),
                            theirs: t.replacement.clone(),
                        });
                    }
                    base_idx = end;
                    oi += 1;
                    ti += 1;
                }
            }
            (Some(o), None) => {
                copy_base(&base_lines, &mut merged, &mut base_idx, o.base_start);
                apply_change(&mut merged, o);
                total_changed_lines += change_span(o);
                base_idx = o.base_end;
                oi += 1;
            }
            (None, Some(t)) => {
                copy_base(&base_lines, &mut merged, &mut base_idx, t.base_start);
                apply_change(&mut merged, t);
                total_changed_lines += change_span(t);
                base_idx = t.base_end;
                ti += 1;
            }
            (None, None) => break,
        }
    }

    // 拷贝 base 尾部。
    while base_idx < base_lines.len() {
        merged.push(base_lines[base_idx].clone());
        base_idx += 1;
    }

    let conflict_score = if total_changed_lines == 0 {
        0.0
    } else {
        conflict_lines as f64 / total_changed_lines as f64
    };
    let has_conflicts = !conflicts.is_empty();
    let result = MergeResult {
        merged_text: merged.join("\n"),
        conflicts,
        conflict_score,
        base_lines: base_lines.len(),
        ours_lines: our_lines.len(),
        theirs_lines: their_lines.len(),
        merged_lines: merged.len(),
        has_conflicts,
    };
    if has_conflicts {
        MergeOutcome::Conflicted(result)
    } else {
        MergeOutcome::Clean(result)
    }
}

/// 三路合并（接收 [`MergeRequest`]）。
pub fn three_way_merge_request(req: &MergeRequest) -> MergeOutcome {
    three_way_merge(&req.base, &req.ours, &req.theirs)
}

fn clean(base: &str, ours: &str, theirs: &str, merged: &str) -> MergeOutcome {
    let base_lines = split_lines(base);
    let our_lines = split_lines(ours);
    let their_lines = split_lines(theirs);
    let merged_lines = split_lines(merged);
    MergeOutcome::Clean(MergeResult {
        merged_text: merged.to_string(),
        conflicts: Vec::new(),
        conflict_score: 0.0,
        base_lines: base_lines.len(),
        ours_lines: our_lines.len(),
        theirs_lines: their_lines.len(),
        merged_lines: merged_lines.len(),
        has_conflicts: false,
    })
}

/// 拷贝 base[start..target] 到 merged，推进 base_idx。
fn copy_base(
    base_lines: &[String],
    merged: &mut Vec<String>,
    base_idx: &mut usize,
    target: usize,
) {
    while *base_idx < target && *base_idx < base_lines.len() {
        merged.push(base_lines[*base_idx].clone());
        *base_idx += 1;
    }
}

/// 应用一个 change 的 replacement 到 merged。
fn apply_change(merged: &mut Vec<String>, c: &Change) {
    merged.extend(c.replacement.iter().cloned());
}

/// 一个 change 涉及的行数（取 base 跨度与 replacement 长度的较大值）。
fn change_span(c: &Change) -> usize {
    (c.base_end - c.base_start).max(c.replacement.len())
}

/// 重叠区的行数（取 base 跨度与 replacement 长度的较大值）。
fn change_span_region(start: usize, end: usize, replacement: &[String]) -> usize {
    (end - start).max(replacement.len())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn both_sides_unchanged_returns_base() {
        let out = three_way_merge("a\nb\nc", "a\nb\nc", "a\nb\nc");
        assert!(!out.has_conflicts());
        let r = out.into_result();
        assert_eq!(r.merged_text, "a\nb\nc");
    }

    #[test]
    fn both_sides_same_change_takes_it() {
        let out = three_way_merge("a\nb\nc", "a\nB\nc", "a\nB\nc");
        assert!(!out.has_conflicts());
        let r = out.into_result();
        assert_eq!(r.merged_text, "a\nB\nc");
    }

    #[test]
    fn ours_unchanged_takes_theirs() {
        let out = three_way_merge("a\nb\nc", "a\nb\nc", "a\nb\nC");
        assert!(!out.has_conflicts());
        let r = out.into_result();
        assert_eq!(r.merged_text, "a\nb\nC");
    }

    #[test]
    fn theirs_unchanged_takes_ours() {
        let out = three_way_merge("a\nb\nc", "A\nb\nc", "a\nb\nc");
        assert!(!out.has_conflicts());
        let r = out.into_result();
        assert_eq!(r.merged_text, "A\nb\nc");
    }

    #[test]
    fn non_overlapping_changes_both_applied() {
        // ours 改第 1 行，theirs 改第 3 行，互不干扰。
        let out = three_way_merge("a\nb\nc\nd", "A\nb\nc\nd", "a\nb\nc\nD");
        assert!(!out.has_conflicts());
        let r = out.into_result();
        assert_eq!(r.merged_text, "A\nb\nc\nD");
    }

    #[test]
    fn overlapping_different_changes_produce_conflict() {
        // 两边都改同一行但改法不同 → 冲突。
        let out = three_way_merge("a\nb\nc", "a\nB1\nc", "a\nB2\nc");
        assert!(out.has_conflicts());
        let r = out.into_result();
        assert!(r.merged_text.contains("<<<<<<< ours"));
        assert!(r.merged_text.contains("======="));
        assert!(r.merged_text.contains(">>>>>>> theirs"));
        assert!(r.merged_text.contains("B1"));
        assert!(r.merged_text.contains("B2"));
        assert_eq!(r.conflicts.len(), 1);
        assert!(r.conflict_score > 0.0);
    }

    #[test]
    fn overlapping_same_change_no_conflict() {
        // 两边都改同一行且改法相同 → 无冲突。
        let out = three_way_merge("a\nb\nc", "a\nB\nc", "a\nB\nc");
        assert!(!out.has_conflicts());
        let r = out.into_result();
        assert_eq!(r.merged_text, "a\nB\nc");
    }

    #[test]
    fn one_side_inserts_other_unchanged_takes_insert() {
        // ours 在末尾插入，theirs 不变 → 取 ours。
        let out = three_way_merge("a\nb", "a\nb\nc", "a\nb");
        assert!(!out.has_conflicts());
        let r = out.into_result();
        assert_eq!(r.merged_text, "a\nb\nc");
    }

    #[test]
    fn non_overlapping_inserts_both_applied() {
        // ours 在第 1 行后插入 X，theirs 在末尾插入 Z。
        let base = "a\nb";
        let ours = "a\nX\nb";
        let theirs = "a\nb\nZ";
        let out = three_way_merge(base, ours, theirs);
        assert!(!out.has_conflicts());
        let r = out.into_result();
        // 两个插入都应保留。
        assert!(r.merged_text.contains("X"));
        assert!(r.merged_text.contains("Z"));
    }

    #[test]
    fn conflict_score_zero_when_no_conflicts() {
        let out = three_way_merge("a\nb\nc", "A\nb\nc", "a\nb\nC");
        let r = out.into_result();
        assert_eq!(r.conflict_score, 0.0);
    }

    #[test]
    fn conflict_score_positive_when_conflicts() {
        let out = three_way_merge("a\nb\nc", "a\nB1\nc", "a\nB2\nc");
        let r = out.into_result();
        assert!(r.conflict_score > 0.0 && r.conflict_score <= 1.0);
    }

    #[test]
    fn empty_base_both_insert_different_no_conflict() {
        // 空基线，ours 与 theirs 都新增但内容不同：因 ours==base 不成立、
        // theirs==base 不成立、ours!=theirs → 进入合并。两边 change 都在
        // base 位置 0（纯插入），重叠 → 冲突。
        let out = three_way_merge("", "X", "Y");
        assert!(out.has_conflicts());
    }

    #[test]
    fn merge_request_works() {
        let req = MergeRequest {
            base: "a\nb".into(),
            ours: "A\nb".into(),
            theirs: "a\nB".into(),
            trace_id: None,
        };
        let out = three_way_merge_request(&req);
        assert!(!out.has_conflicts());
        let r = out.into_result();
        assert_eq!(r.merged_text, "A\nB");
    }
}
