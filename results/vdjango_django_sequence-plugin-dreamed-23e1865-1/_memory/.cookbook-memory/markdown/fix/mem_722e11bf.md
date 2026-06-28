---
type: Fix
title: mem_722e11bf
description: When overriding as_sql() in Django datetime function subclasses (TruncDate/TruncTime), use self.get_tzname() instead of hardcoding timezone.get_current_timezone_name() to respect the tzinfo parameter.
resource: 'memeval://memory/mem_722e11bf'
tags:
- django
- timezone
- database functions
timestamp: '2026-06-27T00:02:03.667984+00:00'
x_item_id: mem_722e11bf
x_relevancy: 0.9
x_version: 1
x_session_id: 3ae00611-729f-45df-b919-acb9ea1f4435
x_source: daydream
x_tokens: 50
---

When overriding as_sql() in Django datetime function subclasses (TruncDate/TruncTime), use self.get_tzname() instead of hardcoding timezone.get_current_timezone_name() to respect the tzinfo parameter.
