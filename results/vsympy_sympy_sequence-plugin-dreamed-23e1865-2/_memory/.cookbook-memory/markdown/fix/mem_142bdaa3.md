---
type: Fix
title: mem_142bdaa3
description: When row_join validates row count, a matrix with self.cols == 0 is a null matrix — skip the row mismatch error because it can be reshaped to match any partner.
resource: 'memeval://memory/mem_142bdaa3'
tags:
- sympy
- matrices
- null-matrix
- join
timestamp: '2026-06-27T23:19:54.438952+00:00'
x_item_id: mem_142bdaa3
x_relevancy: 0.9
x_version: 1
x_session_id: 442fcb4a-5646-4f76-8aa5-b686b89feb62
x_source: daydream
x_tokens: 49
---

When row_join validates row count, a matrix with self.cols == 0 is a null matrix — skip the row mismatch error because it can be reshaped to match any partner. Same for col_join when self.rows == 0.
