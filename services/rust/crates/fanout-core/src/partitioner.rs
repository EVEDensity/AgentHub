//! 一致性哈希分区器：把 partition_key 映射到 `[0, partitions)` 区间。
//!
//! 用 FNV-1a 而非 sha2/crc32，避免额外依赖；分布均匀性足够（fanout 场景
//! 不要求密码学强度）。分区数可动态调整（auto-scale）。
//!
//! 分区数推荐（与原 stub 行为一致，保留向后兼容）：
//! - 0..=999 活跃订阅者 → 8 分区
//! - 1000..=4999 → 16 分区
//! - 5000+ → 32 分区

/// 哈希分区器。
#[derive(Debug, Clone)]
pub struct HashPartitioner {
    partitions: usize,
}

impl HashPartitioner {
    /// 创建分区器。`partitions` 为 0 时退化为 1。
    pub fn new(partitions: usize) -> Self {
        Self {
            partitions: partitions.max(1),
        }
    }

    /// 计算 key 所属分区。
    pub fn partition(&self, key: &str) -> usize {
        if self.partitions == 1 {
            return 0;
        }
        let h = fnv1a_64(key.as_bytes());
        (h % self.partitions as u64) as usize
    }

    /// 当前分区数。
    pub fn partitions(&self) -> usize {
        self.partitions
    }

    /// 调整分区数（auto-scale 时调用）。
    pub fn resize(&mut self, partitions: usize) {
        self.partitions = partitions.max(1);
    }
}

/// FNV-1a 64-bit 哈希。
fn fnv1a_64(bytes: &[u8]) -> u64 {
    let mut hash: u64 = 0xcbf29ce484222325;
    for b in bytes {
        hash ^= *b as u64;
        hash = hash.wrapping_mul(0x100000001b3);
    }
    hash
}

/// 根据活跃订阅者数推荐分区数。
///
/// 阈值（与原 stub 行为一致）：
/// - 0..=999 → 8
/// - 1000..=4999 → 16
/// - 5000+ → 32
pub fn recommended_partition_count(active_sessions: usize) -> usize {
    match active_sessions {
        0..=999 => 8,
        1000..=4999 => 16,
        _ => 32,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    #[test]
    fn partition_is_deterministic() {
        let p = HashPartitioner::new(16);
        let a = p.partition("session-abc");
        let b = p.partition("session-abc");
        assert_eq!(a, b);
    }

    #[test]
    fn different_keys_hit_multiple_partitions() {
        let p = HashPartitioner::new(8);
        let mut buckets: HashMap<usize, usize> = HashMap::new();
        for i in 0..200 {
            let key = format!("key-{}", i);
            *buckets.entry(p.partition(&key)).or_default() += 1;
        }
        // 至少 4 个分区被命中（分布均匀性粗校验）。
        assert!(buckets.len() >= 4);
    }

    #[test]
    fn single_partition_returns_zero() {
        let p = HashPartitioner::new(1);
        assert_eq!(p.partition("anything"), 0);
    }

    #[test]
    fn zero_partitions_normalized_to_one() {
        let p = HashPartitioner::new(0);
        assert_eq!(p.partitions(), 1);
        assert_eq!(p.partition("x"), 0);
    }

    #[test]
    fn resize_changes_partition_count() {
        let mut p = HashPartitioner::new(8);
        p.resize(16);
        assert_eq!(p.partitions(), 16);
    }

    #[test]
    fn recommended_partitions_match_thresholds() {
        assert_eq!(recommended_partition_count(0), 8);
        assert_eq!(recommended_partition_count(999), 8);
        assert_eq!(recommended_partition_count(1000), 16);
        assert_eq!(recommended_partition_count(4999), 16);
        assert_eq!(recommended_partition_count(5000), 32);
        assert_eq!(recommended_partition_count(99999), 32);
    }

    #[test]
    fn distribution_is_reasonably_uniform() {
        // 10000 key × 16 分区，每分区应占 ~625 ± 100。
        let p = HashPartitioner::new(16);
        let mut buckets = vec![0u64; 16];
        for i in 0..10000 {
            let key = format!("tenant-session-{}", i);
            buckets[p.partition(&key)] += 1;
        }
        let max = *buckets.iter().max().unwrap();
        let min = *buckets.iter().min().unwrap();
        assert!(max < 800, "max={} too high", max);
        assert!(min > 400, "min={} too low", min);
    }
}
