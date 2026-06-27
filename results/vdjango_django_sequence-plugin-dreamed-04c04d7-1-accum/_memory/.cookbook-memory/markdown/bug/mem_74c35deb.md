---
type: Bug
title: mem_74c35deb
description: 'When validating Meta.ordering in Django model checks, "pk" is a special alias for the primary key — it will never be found by get_field(), so treat it as valid in both simple and __-separated lookups.'
resource: 'memeval://memory/mem_74c35deb'
tags:
- django
- model_checks
- ordering_validation
timestamp: '2026-06-27T16:10:49.501972+00:00'
x_item_id: mem_74c35deb
x_relevancy: 1.0
x_version: 1
x_session_id: caf09beb-2f5a-4190-9a8f-682663f5f1c9
x_source: daydream
x_tokens: 50
---

When validating Meta.ordering in Django model checks, "pk" is a special alias for the primary key — it will never be found by get_field(), so treat it as valid in both simple and __-separated lookups.
