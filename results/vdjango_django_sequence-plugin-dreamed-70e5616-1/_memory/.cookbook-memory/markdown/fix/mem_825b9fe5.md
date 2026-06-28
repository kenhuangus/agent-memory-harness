---
type: Fix
title: mem_825b9fe5
description: When ForeignKey.validate checks for existence of a related object, use _base_manager instead of _default_manager to avoid false negatives from custom manager filters (e.g., soft-delete, archive).
resource: 'memeval://memory/mem_825b9fe5'
timestamp: '2026-06-28T01:38:13.935937+00:00'
x_item_id: mem_825b9fe5
x_relevancy: 1.0
x_version: 1
x_session_id: 187a068b-a45d-44f2-9f99-d75f5bd85502
x_source: daydream
x_tokens: 48
---

When ForeignKey.validate checks for existence of a related object, use _base_manager instead of _default_manager to avoid false negatives from custom manager filters (e.g., soft-delete, archive).
