---
type: Workaround
title: mem_f3fc0295
description: 'When capturing canvas frames from headless Chromium, use `canvas.toDataURL()` instead of `page.locator(''#cv'').screenshot()` — element screenshots silently drop `shadowBlur`/glow layers.'
resource: 'memeval://memory/mem_f3fc0295'
tags:
- canvas
- playwright
- screenshot
timestamp: '2026-06-28T23:15:55.841256+00:00'
x_item_id: mem_f3fc0295
x_relevancy: 0.95
x_version: 1
x_session_id: fd9ff585-902d-4ae1-948c-6c6a4446e2c1
x_source: daydream
x_tokens: 46
---

When capturing canvas frames from headless Chromium, use `canvas.toDataURL()` instead of `page.locator('#cv').screenshot()` — element screenshots silently drop `shadowBlur`/glow layers.
