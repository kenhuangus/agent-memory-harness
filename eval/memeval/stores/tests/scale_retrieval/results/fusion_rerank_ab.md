# Track A — Route-to-one vs Fuse-all vs Fuse-all+Rerank

Caption: Offline deterministic A/B over the scale-eval retained cases (hashing embedder, lexical MockReranker; quality=1500, filler=100, cases=291). Fusion fans out to all backends and merges (RRF); rerank re-scores the fused top-50. The SEMANTIC rerank LIFT requires the captained Voyage run; this measures the route-vs-fuse mechanism + lexical reordering only.

## Overall

config | recall@1 | recall@5 | recall@10 | MRR@10
--- | ---: | ---: | ---: | ---:
route_speed | 0.7938 | 0.8694 | 0.8763 | 0.8194
fusion_rrf | 0.8041 | 0.9828 | 0.9966 | 0.859
fusion_rrf_rerank | 0.8179 | 0.9897 | 1.0 | 0.8692

## By lens (recall@10 / MRR@10)

lens | route_speed | fusion_rrf | fusion_rrf_rerank
--- | ---: | ---: | ---:
lexical | 1.0/0.9882 | 1.0/1.0 | 1.0/1.0
mixed_adversarial | 0.8462/0.7372 | 1.0/0.8162 | 1.0/0.8897
multi_hop_relational | 0.3478/0.1109 | 0.9783/0.2638 | 1.0/0.2659
temporal_versioned | 1.0/1.0 | 1.0/1.0 | 1.0/1.0
