//! Patch 应用：在 base 上重放 [`DiffOp`] 序列重建修订文本。
//!
//! 安全策略：
//! - 先校验 `base_hash`，不匹配则拒绝（避免在脏 base 上误用旧 patch）。
//! - 逐 op 校验 context：Equal/Delete 要求 base 当前行与 op 行一致，否则拒绝。
//! - 校验失败返回 `ok=false` + error，不输出半成品。

use crate::types::{content_hash, split_lines, DiffOp, Patch, PatchResult};

/// 在 `base` 上应用 `patch`，返回 [`PatchResult`]。
pub fn apply_patch(base: &str, patch: &Patch) -> PatchResult {
    // 1. 校验 base 完整性。
    let actual_hash = content_hash(base);
    if actual_hash != patch.base_hash {
        return PatchResult {
            ok: false,
            result_text: String::new(),
            applied_ops: 0,
            error: Some(format!(
                "base hash mismatch: expected {}, got {}",
                patch.base_hash, actual_hash
            )),
        };
    }

    let base_lines = split_lines(base);
    let mut result: Vec<String> = Vec::with_capacity(base_lines.len());
    let mut base_idx = 0usize;
    let mut applied = 0usize;

    for op in &patch.ops {
        match op {
            DiffOp::Equal(line) => {
                if base_idx >= base_lines.len() {
                    return reject("patch context mismatch: equal op beyond base end", applied);
                }
                if base_lines[base_idx] != *line {
                    return reject(
                        &format!(
                            "patch context mismatch at base line {}: expected {:?}, got {:?}",
                            base_idx,
                            base_lines[base_idx],
                            line
                        ),
                        applied,
                    );
                }
                result.push(base_lines[base_idx].clone());
                base_idx += 1;
            }
            DiffOp::Delete(line) => {
                if base_idx >= base_lines.len() {
                    return reject("patch context mismatch: delete op beyond base end", applied);
                }
                if base_lines[base_idx] != *line {
                    return reject(
                        &format!(
                            "patch delete mismatch at base line {}: expected {:?}, got {:?}",
                            base_idx,
                            base_lines[base_idx],
                            line
                        ),
                        applied,
                    );
                }
                // 删除：跳过 base 当前行，不输出。
                base_idx += 1;
            }
            DiffOp::Insert(line) => {
                result.push(line.clone());
            }
        }
        applied += 1;
    }

    // 拷贝 patch 未覆盖的 base 尾部（patch 可能只描述了局部变更）。
    while base_idx < base_lines.len() {
        result.push(base_lines[base_idx].clone());
        base_idx += 1;
    }

    PatchResult {
        ok: true,
        result_text: result.join("\n"),
        applied_ops: applied,
        error: None,
    }
}

fn reject(msg: &str, applied: usize) -> PatchResult {
    PatchResult {
        ok: false,
        result_text: String::new(),
        applied_ops: applied,
        error: Some(msg.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::diff::diff_lines;
    use crate::types::Patch;

    fn patch_from_diff(base: &str, revised: &str) -> Patch {
        Patch {
            base_hash: content_hash(base),
            ops: diff_lines(base, revised),
        }
    }

    #[test]
    fn apply_clean_patch_roundtrips() {
        let base = "a\nb\nc\nd";
        let revised = "a\nB\nc\nd";
        let patch = patch_from_diff(base, revised);
        let res = apply_patch(base, &patch);
        assert!(res.ok, "{:?}", res.error);
        assert_eq!(res.result_text, revised);
    }

    #[test]
    fn apply_insert_only_patch() {
        let base = "a\nc";
        let revised = "a\nb\nc";
        let patch = patch_from_diff(base, revised);
        let res = apply_patch(base, &patch);
        assert!(res.ok);
        assert_eq!(res.result_text, revised);
    }

    #[test]
    fn apply_delete_only_patch() {
        let base = "a\nb\nc";
        let revised = "a\nc";
        let patch = patch_from_diff(base, revised);
        let res = apply_patch(base, &patch);
        assert!(res.ok);
        assert_eq!(res.result_text, revised);
    }

    #[test]
    fn apply_full_replace_patch() {
        let base = "x\ny\nz";
        let revised = "p\nq";
        let patch = patch_from_diff(base, revised);
        let res = apply_patch(base, &patch);
        assert!(res.ok);
        assert_eq!(res.result_text, revised);
    }

    #[test]
    fn apply_rejects_dirty_base() {
        let base = "a\nb\nc";
        let revised = "a\nB\nc";
        let patch = patch_from_diff(base, revised);
        // 在被修改过的 base 上应用 → hash 不匹配。
        let dirty = "a\nb\nC";
        let res = apply_patch(dirty, &patch);
        assert!(!res.ok);
        assert!(res.error.unwrap().contains("hash mismatch"));
    }

    #[test]
    fn apply_rejects_context_mismatch_with_matching_hash() {
        // 构造一个 base_hash 正确但 op context 与实际 base 不符的 patch。
        let base = "a\nb\nc";
        let mut patch = patch_from_diff(base, "a\nB\nc");
        // 篡改 Equal 行使其与 base 不符，但保留 base_hash。
        patch.ops = vec![
            DiffOp::Equal("WRONG".into()),
            DiffOp::Equal("b".into()),
            DiffOp::Equal("c".into()),
        ];
        let res = apply_patch(base, &patch);
        assert!(!res.ok);
        assert!(res.error.unwrap().contains("context mismatch"));
    }

    #[test]
    fn apply_empty_patch_returns_base() {
        let base = "a\nb";
        let patch = Patch {
            base_hash: content_hash(base),
            ops: vec![],
        };
        let res = apply_patch(base, &patch);
        assert!(res.ok);
        assert_eq!(res.result_text, base);
    }

    #[test]
    fn apply_partial_patch_keeps_tail() {
        // patch 只描述前半段变更，base 尾部应原样保留。
        let base = "a\nb\nc\nd\ne";
        let patch = Patch {
            base_hash: content_hash(base),
            ops: vec![
                DiffOp::Delete("a".into()),
                DiffOp::Insert("A".into()),
                DiffOp::Equal("b".into()),
            ],
        };
        let res = apply_patch(base, &patch);
        assert!(res.ok, "{:?}", res.error);
        assert_eq!(res.result_text, "A\nb\nc\nd\ne");
    }
}
