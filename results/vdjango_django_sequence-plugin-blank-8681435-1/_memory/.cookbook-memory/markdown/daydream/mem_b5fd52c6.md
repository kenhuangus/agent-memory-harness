---
type: daydream
title: mem_b5fd52c6
description: 'Fixed in django/apps/registry.py: get_app_config() and get_registered_model() now fall back to case-insensitive app_label lookup to handle …'
resource: 'memeval://memory/mem_b5fd52c6'
tags:
- django
- code-change
- registry.py
- case-insensitive
timestamp: '2026-06-25T22:08:55.961504+00:00'
x_item_id: mem_b5fd52c6
x_relevancy: 0.9
x_version: 1
x_session_id: bbe46570-82df-486b-950b-605d7b1b5431
x_source: daydream
x_tokens: 50
x_metadata_json: '{"extracted_from": "bbe46570-82df-486b-950b-605d7b1b5431"}'
---

Fixed in django/apps/registry.py: get_app_config() and get_registered_model() now fall back to case-insensitive app_label lookup to handle mixed-case or legacy-lowercase app labels in lazy references.
