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
backend_markdown | ok | 0.808 | 0.814 | 0.825 | 0.812 | 0.815 | 250780 | 323927 | 3813.993 | 895006 | 5639096 | 694.252 |
backend_vector_hash | ok | 0.625 | 0.794 | 0.825 | 0.701 | 0.732 | 148684 | 214976 | 6210.372 | 84711351 | 89021976 | 13.167 |
backend_graph_bfs | ok | 0.821 | 0.997 | 1.000 | 0.884 | 0.926 | 26757 | 47905 | 33206.581 | 9302439 | 10327818 | 114.851 |
router_speed | ok | 0.794 | 0.869 | 0.876 | 0.819 | 0.838 | 160497 | 295402 | 6203.747 | 9251271 | 91735014 | 24.289 |
router_cascade | ok | 0.701 | 0.794 | 0.818 | 0.739 | 0.758 | 150298 | 262027 | 6831.268 | 83448092 | 93176139 | 19.237 |
fusion_rrf | ok | 0.804 | 0.983 | 0.997 | 0.859 | 0.902 | 145134 | 258870 | 6908.224 | 94042667 | 100297347 | 11.699 |
fusion_score | ok | 0.739 | 0.856 | 0.869 | 0.795 | 0.811 | 148498 | 263800 | 6809.571 | 93340738 | 99474643 | 11.774 |
future_vector_sqlite_vec | skip | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | opt-in: MEMEVAL_LOCAL_ANN=1 unset
fusion_local_ann | skip | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | opt-in: MEMEVAL_LOCAL_ANN=1 unset
fusion_local_ann_rerank | skip | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | opt-in: MEMEVAL_LOCAL_ANN=1 unset
backend_fts5 | ok | 0.808 | 0.842 | 0.842 | 0.825 | 0.829 | 43527 | 111707 | 19699.974 | 736794 | 5171605 | 767.198 |
fusion_fts5_rrf | ok | 0.808 | 0.990 | 0.997 | 0.864 | 0.906 | 49418 | 186814 | 13189.233 | 93949500 | 99939087 | 11.739 |
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
