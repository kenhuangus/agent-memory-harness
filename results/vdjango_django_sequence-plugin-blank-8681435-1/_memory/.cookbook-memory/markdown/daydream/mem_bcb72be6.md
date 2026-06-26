---
type: daydream
title: mem_bcb72be6
description: In django/db/models/functions/datetime.py, TruncTime.as_sql() was fixed to use self.get_tzname() instead of timezone.get_current_timezone_n…
resource: 'memeval://memory/mem_bcb72be6'
tags:
- django
- datetime
- tzinfo
- TruncTime
timestamp: '2026-06-25T22:21:01.835008+00:00'
x_item_id: mem_bcb72be6
x_relevancy: 0.85
x_version: 1
x_session_id: 4ed3df34-789e-45d3-95a3-3fd3ce22377b
x_source: daydream
x_tokens: 46
x_metadata_json: '{"extracted_from": "4ed3df34-789e-45d3-95a3-3fd3ce22377b"}'
---

In django/db/models/functions/datetime.py, TruncTime.as_sql() was fixed to use self.get_tzname() instead of timezone.get_current_timezone_name(), so the tzinfo parameter is now respected.
