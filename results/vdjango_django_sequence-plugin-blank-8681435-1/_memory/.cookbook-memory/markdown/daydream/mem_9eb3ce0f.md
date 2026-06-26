---
type: daydream
title: mem_9eb3ce0f
description: In django/db/models/functions/datetime.py, TruncDate.as_sql() was fixed to use self.get_tzname() instead of timezone.get_current_timezone_n…
resource: 'memeval://memory/mem_9eb3ce0f'
tags:
- django
- datetime
- tzinfo
- TruncDate
timestamp: '2026-06-25T22:21:01.835008+00:00'
x_item_id: mem_9eb3ce0f
x_relevancy: 0.85
x_version: 1
x_session_id: 4ed3df34-789e-45d3-95a3-3fd3ce22377b
x_source: daydream
x_tokens: 46
x_metadata_json: '{"extracted_from": "4ed3df34-789e-45d3-95a3-3fd3ce22377b"}'
---

In django/db/models/functions/datetime.py, TruncDate.as_sql() was fixed to use self.get_tzname() instead of timezone.get_current_timezone_name(), so the tzinfo parameter is now respected.
