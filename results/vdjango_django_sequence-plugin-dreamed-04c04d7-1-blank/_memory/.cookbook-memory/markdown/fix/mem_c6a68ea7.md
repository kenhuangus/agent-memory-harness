---
type: Fix
title: mem_c6a68ea7
description: When adding __iter__ to a Paginator-like class, yield self.page(page_num) for page_num in self.page_range to provide Pythonic iteration over all pages.
resource: 'memeval://memory/mem_c6a68ea7'
tags:
- django
- paginator
- iteration
timestamp: '2026-06-27T14:54:38.803881+00:00'
x_item_id: mem_c6a68ea7
x_relevancy: 0.95
x_version: 1
x_session_id: b4398576-0267-4713-b226-674a5835c6ee
x_source: daydream
x_tokens: 37
---

When adding __iter__ to a Paginator-like class, yield self.page(page_num) for page_num in self.page_range to provide Pythonic iteration over all pages.
