pub struct FusionWeights {
    pub bm25: f32,
    pub dense: f32,
    pub rerank: f32,
    pub freshness: f32,
}

pub fn default_fusion_weights() -> FusionWeights {
    FusionWeights {
        bm25: 0.30,
        dense: 0.35,
        rerank: 0.25,
        freshness: 0.10,
    }
}
