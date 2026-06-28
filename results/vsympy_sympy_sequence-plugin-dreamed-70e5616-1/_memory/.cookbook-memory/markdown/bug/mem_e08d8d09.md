---
type: Bug
title: mem_e08d8d09
description: 'When the sub/sup dictionaries at module level contain None entries because U() returned None, the pretty_list fallback path crashes on ''''.join([None]) and returns None, suppressing all formatting.'
resource: 'memeval://memory/mem_e08d8d09'
tags:
- pretty-printing
- unicode
timestamp: '2026-06-28T00:29:13.931848+00:00'
x_item_id: mem_e08d8d09
x_relevancy: 0.7
x_version: 1
x_session_id: e7ce704d-acc1-467e-a610-9f57f2909ffa
x_source: daydream
x_tokens: 49
---

When the sub/sup dictionaries at module level contain None entries because U() returned None, the pretty_list fallback path crashes on ''.join([None]) and returns None, suppressing all formatting.
