---
type: Fix
title: mem_0ea3ee7a
description: When Django _check_ordering() traverses related-field ordering chains, set the current model to None after a non-relation field to reject invalid multi-hop lookups like parent__field1__field2.
resource: 'memeval://memory/mem_0ea3ee7a'
tags:
- django
- model-validation
- ordering
timestamp: '2026-06-27T05:17:04.302609+00:00'
x_item_id: mem_0ea3ee7a
x_relevancy: 0.8
x_version: 1
x_session_id: 9e85368a-b9aa-4f25-aaed-eefb2a7a00ad
x_source: daydream
x_tokens: 48
---

When Django _check_ordering() traverses related-field ordering chains, set the current model to None after a non-relation field to reject invalid multi-hop lookups like parent__field1__field2.
