---
type: Memory
title: mem_b4fdec5d
description: TruncDate and TruncTime in django.db.models.functions.datetime unconditionally used timezone.get_current_timezone_name() instead of self.get_tzname(), causing the tzinfo parameter to be ignored.
resource: 'memeval://memory/mem_b4fdec5d'
tags:
- django
- bug
- timezone
timestamp: '2026-06-27T06:57:24.249940+00:00'
x_item_id: mem_b4fdec5d
x_relevancy: 0.95
x_version: 1
x_session_id: d01597cc-99c6-402a-8db2-466f19186973
x_source: daydream
x_tokens: 48
---

TruncDate and TruncTime in django.db.models.functions.datetime unconditionally used timezone.get_current_timezone_name() instead of self.get_tzname(), causing the tzinfo parameter to be ignored.
