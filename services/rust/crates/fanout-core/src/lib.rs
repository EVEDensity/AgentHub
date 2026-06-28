pub fn recommended_partition_count(active_sessions: usize) -> usize {
    match active_sessions {
        0..=999 => 8,
        1000..=4999 => 16,
        _ => 32,
    }
}
