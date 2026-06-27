---
type: Bug
title: mem_782fb28d
description: When using Python regex for string validation (e.g., username, email), prefer \A and \Z over ^ and $ because $ matches a trailing newline, allowing invalid input to pass validation.
resource: 'memeval://memory/mem_782fb28d'
tags:
- security
- validation
- regex
timestamp: '2026-06-26T22:28:44.990939+00:00'
x_item_id: mem_782fb28d
x_relevancy: 0.9
x_version: 1
x_session_id: 7aa6b42a-6c89-4860-8ec1-b052bb327013
x_source: daydream
x_tokens: 45
---

When using Python regex for string validation (e.g., username, email), prefer \A and \Z over ^ and $ because $ matches a trailing newline, allowing invalid input to pass validation.
