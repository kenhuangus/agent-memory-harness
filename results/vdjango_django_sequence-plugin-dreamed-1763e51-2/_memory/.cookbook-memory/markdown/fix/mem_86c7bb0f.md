---
type: Fix
title: mem_86c7bb0f
description: 'When a regex lookahead validates a component''s format but the overall pattern allows negative values, the lookahead must also allow optional minus signs to avoid rejecting valid negative inputs.'
resource: 'memeval://memory/mem_86c7bb0f'
timestamp: '2026-06-27T15:39:30.383673+00:00'
x_item_id: mem_86c7bb0f
x_relevancy: 0.9
x_version: 1
x_session_id: eb3669a3-7fc2-4d92-9411-c9587b650330
x_source: daydream
x_tokens: 48
---

When a regex lookahead validates a component's format but the overall pattern allows negative values, the lookahead must also allow optional minus signs to avoid rejecting valid negative inputs.
