---
type: Fix
title: mem_be4c5c1e
description: 'When splitting a dotted path like ''module.Outer.Inner'' to find the module boundary, check sys.modules for progressively shorter prefixes rather than doing a blind rsplit(''.'',1).'
resource: 'memeval://memory/mem_be4c5c1e'
timestamp: '2026-06-27T16:32:40.005216+00:00'
x_item_id: mem_be4c5c1e
x_relevancy: 0.92
x_version: 1
x_session_id: 08a97d60-a315-4bb5-97ac-a85101c8c975
x_source: daydream
x_tokens: 44
---

When splitting a dotted path like 'module.Outer.Inner' to find the module boundary, check sys.modules for progressively shorter prefixes rather than doing a blind rsplit('.',1).
