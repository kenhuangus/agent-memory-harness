---
type: Memory
title: mem_cba34050
description: 'The fix for the TruncDate/TruncTime tzinfo bug: replace timezone.get_current_timezone_name() if settings.USE_TZ else None with self.get_tzname() in both as_sql() methods.'
resource: 'memeval://memory/mem_cba34050'
tags:
- django
- fix
- timezone
timestamp: '2026-06-27T06:57:24.249940+00:00'
x_item_id: mem_cba34050
x_relevancy: 0.95
x_version: 1
x_session_id: d01597cc-99c6-402a-8db2-466f19186973
x_source: daydream
x_tokens: 42
---

The fix for the TruncDate/TruncTime tzinfo bug: replace timezone.get_current_timezone_name() if settings.USE_TZ else None with self.get_tzname() in both as_sql() methods.
