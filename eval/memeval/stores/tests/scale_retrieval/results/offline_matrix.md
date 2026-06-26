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
backend_markdown | ok | 0.808 | 0.814 | 0.825 | 0.812 | 0.815 | 1443707 | 1672875 | 679.674 | 948573 | 5995426 | 660.285 | —
backend_vector_hash | ok | 0.625 | 0.794 | 0.825 | 0.701 | 0.732 | 310759 | 566910 | 2789.154 | 94034142 | 100901356 | 11.761 | —
backend_graph_bfs | ok | 0.821 | 0.997 | 1.000 | 0.884 | 0.926 | 159889 | 374980 | 4793.578 | 9248926 | 9704756 | 118.994 | —
router_speed | ok | 0.794 | 0.869 | 0.876 | 0.819 | 0.838 | 335658 | 1612066 | 1456.185 | 9830357 | 99695888 | 22.280 | —
router_cascade | ok | 0.701 | 0.794 | 0.818 | 0.739 | 0.758 | 330856 | 1599933 | 1455.478 | 89188596 | 102376670 | 17.837 | —
fusion_rrf | ok | 0.804 | 0.983 | 0.997 | 0.859 | 0.902 | 325584 | 1559839 | 1475.934 | 105979715 | 112230055 | 10.370 | —
fusion_score | ok | 0.739 | 0.856 | 0.869 | 0.795 | 0.811 | 340560 | 1600527 | 1452.641 | 106513803 | 112127612 | 10.370 | —
future_vector_sqlite_vec | skip | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | opt-in: MEMEVAL_LOCAL_ANN=1 unset
fusion_local_ann | skip | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | opt-in: MEMEVAL_LOCAL_ANN=1 unset
fusion_local_ann_rerank | skip | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | opt-in: MEMEVAL_LOCAL_ANN=1 unset
backend_fts5 | ok | 0.808 | 0.842 | 0.842 | 0.825 | 0.829 | 219903 | 368350 | 4095.716 | 816759 | 5830238 | 664.911 | —
fusion_rrf_with_fts5 | ok | 0.808 | 0.990 | 0.997 | 0.864 | 0.907 | 295483 | 1524143 | 1748.276 | 107464916 | 115190253 | 10.177 | —
fusion_fts5_rrf | ok | 0.808 | 0.990 | 0.997 | 0.864 | 0.906 | 253775 | 472192 | 3757.804 | 104621036 | 112262205 | 10.543 | —
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
