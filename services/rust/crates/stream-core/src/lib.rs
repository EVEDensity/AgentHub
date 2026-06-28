pub struct FlushPolicy {
    pub max_buffered_chunks: usize,
    pub max_flush_interval_ms: u64,
}

pub fn default_flush_policy() -> FlushPolicy {
    FlushPolicy {
        max_buffered_chunks: 12,
        max_flush_interval_ms: 120,
    }
}
