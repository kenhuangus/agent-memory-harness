# Scale Retrieval Matrix

Caption: Offline deterministic matrix baseline using the stdlib hashing embedder; quality=1500, filler=100, candidate=600, retained=291 (lexical=120, semantic_divergence=0, multi_hop_relational=46, temporal_versioned=86, mixed_adversarial=39). Semantic accuracy and the route-vs-fuse accuracy verdict require the captained Voyage run; this artifact is the deterministic mechanism, lexical/relational, and latency baseline.

Mode: `offline`
Quality items: 1500
Filler items: 100
Cases: 291
Retained counts by lens: lexical=120, semantic_divergence=0, multi_hop_relational=46, temporal_versioned=86, mixed_adversarial=39

## Matrix

cell | status | recall@1 | recall@5 | recall@10 | MRR@10 | nDCG@10 | write p50 ns | write p95 ns | write/s | search p50 ns | search p95 ns | search/s | reason
--- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---
backend_markdown | ok | 0.808 | 0.814 | 0.825 | 0.812 | 0.815 | 1417347 | 1713693 | 686.229 | 887535 | 5652358 | 700.855 | —
backend_vector_hash | ok | 0.625 | 0.794 | 0.825 | 0.701 | 0.732 | 440940 | 594507 | 2135.369 | 84920515 | 88716393 | 13.164 | —
backend_graph_bfs | ok | 0.821 | 0.997 | 1.000 | 0.884 | 0.926 | 292037 | 381230 | 3266.761 | 9845181 | 10795331 | 109.924 | —
router_speed | ok | 0.794 | 0.869 | 0.876 | 0.819 | 0.838 | 435157 | 1590907 | 1347.371 | 9708629 | 87861542 | 24.893 | —
router_cascade | ok | 0.701 | 0.794 | 0.818 | 0.739 | 0.758 | 462034 | 1700305 | 1266.822 | 87922608 | 106212360 | 17.982 | —
fusion_rrf | ok | 0.804 | 0.983 | 0.997 | 0.859 | 0.902 | 449393 | 1689121 | 1279.012 | 100873450 | 122777070 | 10.094 | —
fusion_score | ok | 0.739 | 0.856 | 0.869 | 0.795 | 0.811 | 468597 | 1794776 | 1207.422 | 102767207 | 123993512 | 9.901 | —
future_vector_sqlite_vec | skip | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | opt-in: MEMEVAL_LOCAL_ANN=1 unset
fusion_local_ann | skip | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | opt-in: MEMEVAL_LOCAL_ANN=1 unset
fusion_local_ann_rerank | skip | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | opt-in: MEMEVAL_LOCAL_ANN=1 unset
backend_fts5 | ok | 0.808 | 0.842 | 0.842 | 0.825 | 0.829 | 247993 | 492587 | 3375.925 | 813578 | 5838211 | 701.667 | —
fusion_fts5_rrf | ok | 0.808 | 0.990 | 0.997 | 0.864 | 0.906 | 385331 | 560380 | 2494.158 | 100963921 | 121714631 | 10.538 | —
accuracy_voyage | skip | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | captained: MEMEVAL_LIVE unset
accuracy_voyage_rerank | skip | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | captained: MEMEVAL_LIVE unset
fuse_all_voyage_rerank_rrf | skip | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | captained: MEMEVAL_LIVE unset
future_vector_usearch_hnsw | skip | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | future: usearch_hnsw selector not implemented
future_graph_neo4j_phase_b | skip | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | future: native Neo4j graph engine not in matrix yet
future_graph_falkordb | skip | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | future: FalkorDB graph engine not in matrix yet

## Calibration Lift

lens | n | floor recall@10 | floor MRR@10 | target recall@10 | target MRR@10 | recall lift | MRR lift
--- | ---: | ---: | ---: | ---: | ---: | ---: | ---:
mixed_adversarial | 39 | 0.821 | 0.265 | 1.000 | 0.923 | 0.179 | 0.658
multi_hop_relational | 46 | 0.000 | 0.000 | 1.000 | 0.328 | 1.000 | 0.328
temporal_versioned | 86 | 1.000 | 0.500 | 1.000 | 1.000 | 0.000 | 0.500
