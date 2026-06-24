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
backend_markdown | ok | 0.808 | 0.814 | 0.825 | 0.812 | 0.815 | 265455 | 307542 | 3644.484 | 921835 | 5774423 | 679.871 |
backend_vector_hash | ok | 0.625 | 0.794 | 0.825 | 0.701 | 0.732 | 164417 | 235113 | 5628.454 | 89299736 | 93542149 | 12.427 |
backend_graph_bfs | ok | 0.821 | 0.997 | 1.000 | 0.884 | 0.926 | 28925 | 40671 | 32280.125 | 9096081 | 9566931 | 120.332 |
router_speed | ok | 0.794 | 0.869 | 0.876 | 0.819 | 0.838 | 158163 | 273339 | 6451.404 | 9301808 | 93389600 | 23.501 |
router_cascade | ok | 0.701 | 0.794 | 0.818 | 0.739 | 0.758 | 158574 | 280122 | 6354.447 | 88448394 | 98775540 | 18.153 |
fusion_rrf | ok | 0.804 | 0.983 | 0.997 | 0.859 | 0.902 | 158926 | 274731 | 6421.762 | 99950666 | 105238362 | 11.042 |
fusion_score | ok | 0.739 | 0.856 | 0.869 | 0.795 | 0.811 | 165818 | 287600 | 6259.173 | 101325347 | 113289763 | 10.614 |
accuracy_voyage | skip | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | captained: MEMEVAL_LIVE unset
accuracy_voyage_rerank | skip | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | captained: MEMEVAL_LIVE unset
fuse_all_voyage_rerank_rrf | skip | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | captained: MEMEVAL_LIVE unset
future_vector_sqlite_vec | skip | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | future: sqlite_vec selector not implemented
future_vector_usearch_hnsw | skip | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | future: usearch_hnsw selector not implemented
future_graph_neo4j_phase_b | skip | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | future: native Neo4j graph engine not in matrix yet
future_graph_falkordb | skip | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | future: FalkorDB graph engine not in matrix yet
future_lexical_fts5 | skip | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | future: SQLite FTS5 lexical engine not in matrix yet

## Calibration Lift

lens | n | floor recall@10 | floor MRR@10 | target recall@10 | target MRR@10 | recall lift | MRR lift
--- | ---: | ---: | ---: | ---: | ---: | ---: | ---:
mixed_adversarial | 39 | 0.821 | 0.265 | 1.000 | 0.923 | 0.179 | 0.658
multi_hop_relational | 46 | 0.000 | 0.000 | 1.000 | 0.328 | 1.000 | 0.328
temporal_versioned | 86 | 1.000 | 0.500 | 1.000 | 1.000 | 0.000 | 0.500
