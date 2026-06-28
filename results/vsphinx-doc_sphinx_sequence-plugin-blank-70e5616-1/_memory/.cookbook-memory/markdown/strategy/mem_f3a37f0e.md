---
type: Strategy
title: mem_f3a37f0e
description: When processing alternating key/separator lists from re.split, use index parity (even=key, odd=separator) rather than try/except IndexError; it simplifies context-aware decisions about empty keys.
resource: 'memeval://memory/mem_f3a37f0e'
tags:
- code-pattern
timestamp: '2026-06-28T00:00:41.039945+00:00'
x_item_id: mem_f3a37f0e
x_relevancy: 0.7
x_version: 1
x_session_id: 600dba10-f99e-4fa0-9daf-2f530c3de09e
x_source: daydream
x_tokens: 49
---

When processing alternating key/separator lists from re.split, use index parity (even=key, odd=separator) rather than try/except IndexError; it simplifies context-aware decisions about empty keys.
