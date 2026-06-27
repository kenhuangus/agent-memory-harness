---
type: Invariant
title: mem_afd5f258
description: 'When Python code calls str() on a memoryview object, it does not yield the underlying bytes; str(memoryview(b''abc'')) returns ''<memory at 0x...>'' instead of ''abc''.'
resource: 'memeval://memory/mem_afd5f258'
timestamp: '2026-06-26T22:32:44.117188+00:00'
x_item_id: mem_afd5f258
x_relevancy: 0.95
x_version: 1
x_session_id: b37ac83a-3803-4226-85cb-e59a8429a532
x_source: daydream
x_tokens: 50
---

When Python code calls str() on a memoryview object, it does not yield the underlying bytes; str(memoryview(b'abc')) returns '<memory at 0x...>' instead of 'abc'. Use bytes(x) to obtain the raw bytes.
