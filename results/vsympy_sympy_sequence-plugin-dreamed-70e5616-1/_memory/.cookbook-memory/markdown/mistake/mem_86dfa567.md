---
type: Mistake
title: mem_86dfa567
description: When fixing a bug in __rmul__, do not change the order of arguments to mul() — the original g.mul(f) is correct; instead look for missing exception types in the except clause.
resource: 'memeval://memory/mem_86dfa567'
timestamp: '2026-06-28T01:30:09.276678+00:00'
x_item_id: mem_86dfa567
x_relevancy: 0.8
x_version: 1
x_session_id: 95807cf2-b31a-48cf-ac66-01573c1ea6a1
x_source: daydream
x_tokens: 43
---

When fixing a bug in __rmul__, do not change the order of arguments to mul() — the original g.mul(f) is correct; instead look for missing exception types in the except clause.
