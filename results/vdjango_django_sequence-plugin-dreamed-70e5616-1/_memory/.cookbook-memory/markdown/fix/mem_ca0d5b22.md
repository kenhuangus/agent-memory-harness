---
type: Fix
title: mem_ca0d5b22
description: 'When a recursive query-building method receives a `simple_col` parameter, all recursive calls must forward it, or constraint SQL on SQLite/Oracle will mix qualified and unqualified column references.'
resource: 'memeval://memory/mem_ca0d5b22'
tags:
- sql-generation
- constraint-sql
timestamp: '2026-06-27T08:25:24.886934+00:00'
x_item_id: mem_ca0d5b22
x_relevancy: 0.95
x_version: 1
x_session_id: e234fc25-46eb-4e77-92da-9efc85441991
x_source: daydream
x_tokens: 49
---

When a recursive query-building method receives a `simple_col` parameter, all recursive calls must forward it, or constraint SQL on SQLite/Oracle will mix qualified and unqualified column references.
