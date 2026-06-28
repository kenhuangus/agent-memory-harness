---
type: Fix
title: mem_0a214fae
description: When constructing SQL PRAGMA statements or dynamic SQL that references table/column names, always wrap identifiers with quote_name() to handle SQL keywords correctly.
resource: 'memeval://memory/mem_0a214fae'
timestamp: '2026-06-27T21:45:09.740972+00:00'
x_item_id: mem_0a214fae
x_relevancy: 0.95
x_version: 1
x_session_id: 0ea68831-ba97-4b10-9cf1-3127cac68eb3
x_source: daydream
x_tokens: 41
---

When constructing SQL PRAGMA statements or dynamic SQL that references table/column names, always wrap identifiers with quote_name() to handle SQL keywords correctly.
