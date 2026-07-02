//! LCS（最长公共子序列）行级 diff。
//!
//! 算法：
//! 1. 经典 DP 求 base/revise 的 LCS 长度表 `dp[i][j] = LCS(base[0..i], revised[0..j])`。
//! 2. 从 `(m, n)` 回溯：相等→Equal 并 i--/j--；否则按 dp 走向删/插侧。
//! 3. [`diff_to_changes`] 在 op 序列上把相邻 Delete/Insert 聚合成 [`Change`] 区段。
//!
//! 复杂度 O(m·n) 时间与空间。对 8 MiB 文本上限（见 [`DiffConfig`]）足够；
//! 超大文本应换 Myers 差异算法（留待后续迭代）。

use crate::types::{content_hash, split_lines, Change, DiffOp, DiffResult};

/// 计算 base → revised 的行级 diff 操作序列。
pub fn diff_lines(base: &str, revised: &str) -> Vec<DiffOp> {
    let base_lines = split_lines(base);
    let revised_lines = split_lines(revised);
    diff_lines_slices(&base_lines, &revised_lines)
}

/// 对已切分的行切片做 diff。
pub fn diff_lines_slices(base: &[String], revised: &[String]) -> Vec<DiffOp> {
    let m = base.len();
    let n = revised.len();

    // dp[i][j] = LCS(base[0..i], revised[0..j])
    let mut dp = vec![vec![0u32; n + 1]; m + 1];
    for i in 1..=m {
        for j in 1..=n {
            if base[i - 1] == revised[j - 1] {
                dp[i][j] = dp[i - 1][j - 1] + 1;
            } else {
                dp[i][j] = dp[i - 1][j].max(dp[i][j - 1]);
            }
        }
    }

    // 回溯生成 op（逆序），最后 reverse。
    let mut ops: Vec<DiffOp> = Vec::with_capacity(m + n);
    let (mut i, mut j) = (m, n);
    while i > 0 && j > 0 {
        if base[i - 1] == revised[j - 1] {
            ops.push(DiffOp::Equal(base[i - 1].clone()));
            i -= 1;
            j -= 1;
        } else if dp[i - 1][j] >= dp[i][j - 1] {
            ops.push(DiffOp::Delete(base[i - 1].clone()));
            i -= 1;
        } else {
            ops.push(DiffOp::Insert(revised[j - 1].clone()));
            j -= 1;
        }
    }
    while i > 0 {
        ops.push(DiffOp::Delete(base[i - 1].clone()));
        i -= 1;
    }
    while j > 0 {
        ops.push(DiffOp::Insert(revised[j - 1].clone()));
        j -= 1;
    }
    ops.reverse();
    ops
}

/// 返回 LCS 匹配块列表：`(base_start, revised_start, length)`。
/// 用于调试与合并算法的匹配定位。
pub fn lcs_matching_blocks(base: &str, revised: &str) -> Vec<(usize, usize, usize)> {
    let base_lines = split_lines(base);
    let revised_lines = split_lines(revised);
    lcs_matching_blocks_slices(&base_lines, &revised_lines)
}

/// 对已切分行切片求匹配块。
pub fn lcs_matching_blocks_slices(base: &[String], revised: &[String]) -> Vec<(usize, usize, usize)> {
    let ops = diff_lines_slices(base, revised);
    let mut blocks = Vec::new();
    let (mut bi, mut ri) = (0usize, 0usize);
    let mut run_start: Option<(usize, usize)> = None;
    let mut run_len = 0usize;

    let flush = |start: Option<(usize, usize)>, len: usize, blocks: &mut Vec<_>| {
        if len > 0 {
            if let Some((bs, rs)) = start {
                blocks.push((bs, rs, len));
            }
        }
    };

    for op in &ops {
        match op {
            DiffOp::Equal(_) => {
                if run_start.is_none() {
                    run_start = Some((bi, ri));
                    run_len = 0;
                }
                run_len += 1;
                bi += 1;
                ri += 1;
            }
            _ => {
                flush(run_start, run_len, &mut blocks);
                run_start = None;
                run_len = 0;
                match op {
                    DiffOp::Delete(_) => bi += 1,
                    DiffOp::Insert(_) => ri += 1,
                    _ => {}
                }
            }
        }
    }
    flush(run_start, run_len, &mut blocks);
    blocks
}

/// 把 diff op 序列聚合成 [`Change`] 区段列表。
/// 相邻的 Delete/Insert（中间无 Equal）合并为一个 Change。
pub fn diff_to_changes(base: &str, revised: &str) -> Vec<Change> {
    let base_lines = split_lines(base);
    let revised_lines = split_lines(revised);
    diff_to_changes_slices(&base_lines, &revised_lines)
}

/// 对已切分行切片聚合 Change。
pub fn diff_to_changes_slices(base: &[String], revised: &[String]) -> Vec<Change> {
    let ops = diff_lines_slices(base, revised);
    let mut changes = Vec::new();
    let mut base_idx = 0usize;
    let mut change_start: Option<usize> = None;
    let mut delete_count = 0usize;
    let mut replacement: Vec<String> = Vec::new();

    for op in &ops {
        match op {
            DiffOp::Equal(_) => {
                if let Some(start) = change_start.take() {
                    changes.push(Change {
                        base_start: start,
                        base_end: start + delete_count,
                        replacement: std::mem::take(&mut replacement),
                    });
                    delete_count = 0;
                }
                base_idx += 1;
            }
            DiffOp::Delete(_) => {
                if change_start.is_none() {
                    change_start = Some(base_idx);
                }
                delete_count += 1;
                base_idx += 1;
            }
            DiffOp::Insert(line) => {
                if change_start.is_none() {
                    change_start = Some(base_idx);
                }
                replacement.push(line.clone());
            }
        }
    }
    if let Some(start) = change_start.take() {
        changes.push(Change {
            base_start: start,
            base_end: start + delete_count,
            replacement,
        });
    }
    changes
}

/// 构造完整 [`DiffResult`]（含统计与哈希）。
pub fn diff_result(base: &str, revised: &str) -> DiffResult {
    let ops = diff_lines(base, revised);
    let mut equal = 0usize;
    let mut deleted = 0usize;
    let mut inserted = 0usize;
    for op in &ops {
        match op {
            DiffOp::Equal(_) => equal += 1,
            DiffOp::Delete(_) => deleted += 1,
            DiffOp::Insert(_) => inserted += 1,
        }
    }
    DiffResult {
        base_lines: equal + deleted,
        revised_lines: equal + inserted,
        equal_lines: equal,
        deleted_lines: deleted,
        inserted_lines: inserted,
        base_hash: content_hash(base),
        revised_hash: content_hash(revised),
        ops,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identical_text_is_all_equal() {
        let ops = diff_lines("a\nb\nc", "a\nb\nc");
        assert!(ops.iter().all(|o| matches!(o, DiffOp::Equal(_))));
        assert_eq!(ops.len(), 3);
    }

    #[test]
    fn insert_at_end() {
        let ops = diff_lines("a\nb", "a\nb\nc");
        // 末尾插入 c
        assert!(matches!(ops.last(), Some(DiffOp::Insert(s)) if s == "c"));
    }

    #[test]
    fn delete_at_end() {
        let ops = diff_lines("a\nb\nc", "a\nb");
        assert!(matches!(ops.last(), Some(DiffOp::Delete(s)) if s == "c"));
    }

    #[test]
    fn modify_middle_line() {
        let ops = diff_lines("a\nb\nc", "a\nB\nc");
        let has_del = ops.iter().any(|o| matches!(o, DiffOp::Delete(s) if s == "b"));
        let has_ins = ops.iter().any(|o| matches!(o, DiffOp::Insert(s) if s == "B"));
        assert!(has_del && has_ins);
    }

    #[test]
    fn diff_to_changes_single_modify() {
        let changes = diff_to_changes("a\nb\nc", "a\nB\nc");
        assert_eq!(changes.len(), 1);
        let c = &changes[0];
        assert_eq!(c.base_start, 1);
        assert_eq!(c.base_end, 2); // 替换 base 第 1 行
        assert_eq!(c.replacement, vec!["B".to_string()]);
    }

    #[test]
    fn diff_to_changes_pure_insert() {
        let changes = diff_to_changes("a\nc", "a\nb\nc");
        assert_eq!(changes.len(), 1);
        let c = &changes[0];
        assert!(c.is_pure_insert());
        assert_eq!(c.base_start, 1);
        assert_eq!(c.base_end, 1); // 不删 base
        assert_eq!(c.replacement, vec!["b".to_string()]);
    }

    #[test]
    fn diff_to_changes_pure_delete() {
        let changes = diff_to_changes("a\nb\nc", "a\nc");
        assert_eq!(changes.len(), 1);
        let c = &changes[0];
        assert!(c.is_pure_delete());
        assert_eq!(c.base_start, 1);
        assert_eq!(c.base_end, 2);
        assert!(c.replacement.is_empty());
    }

    #[test]
    fn diff_to_changes_identical_is_empty() {
        let changes = diff_to_changes("x\ny", "x\ny");
        assert!(changes.is_empty());
    }

    #[test]
    fn lcs_matching_blocks_finds_runs() {
        let blocks = lcs_matching_blocks("a\nb\nc\nd", "a\nx\nc\nd");
        // a / c d 应是匹配块
        assert!(blocks.iter().any(|&(bs, rs, len)| bs == 0 && rs == 0 && len == 1));
        assert!(blocks.iter().any(|&(bs, rs, len)| bs == 2 && rs == 2 && len == 2));
    }

    #[test]
    fn diff_result_stats_correct() {
        let r = diff_result("a\nb\nc", "a\nB\nc");
        assert_eq!(r.base_lines, 3);
        assert_eq!(r.revised_lines, 3);
        assert_eq!(r.equal_lines, 2);
        assert_eq!(r.deleted_lines, 1);
        assert_eq!(r.inserted_lines, 1);
        assert_eq!(r.base_hash.len(), 16);
    }

    #[test]
    fn empty_texts_diff_cleanly() {
        // "" 按 '\n' 切分得到单个空行 [""]；两端相同 → 无变更。
        let changes = diff_to_changes("", "");
        assert!(changes.is_empty());
        // "" → "new"：替换单个空行（base 视作 1 行，故非纯插入）。
        // 这保证空 base 上双方插入不同内容会被三路合并判为冲突（见 merge 测试）。
        let changes = diff_to_changes("", "new");
        assert_eq!(changes.len(), 1);
        assert_eq!(changes[0].base_start, 0);
        assert_eq!(changes[0].base_end, 1);
        assert_eq!(changes[0].replacement, vec!["new".to_string()]);
    }
}
