---
type: Bug
title: mem_bb3e5d37
description: 'When the add template filter returns empty string for concatenation, the cause is a TypeError caught by the broad ''except Exception: return '''''' — this silently hides lazy proxy operator failures.'
resource: 'memeval://memory/mem_bb3e5d37'
timestamp: '2026-06-28T02:26:26.200126+00:00'
x_item_id: mem_bb3e5d37
x_relevancy: 0.95
x_version: 1
x_session_id: 1a62b621-0ed6-4ed5-890f-f70de7b241c2
x_source: daydream
x_tokens: 48
---

When the add template filter returns empty string for concatenation, the cause is a TypeError caught by the broad 'except Exception: return ''' — this silently hides lazy proxy operator failures.
