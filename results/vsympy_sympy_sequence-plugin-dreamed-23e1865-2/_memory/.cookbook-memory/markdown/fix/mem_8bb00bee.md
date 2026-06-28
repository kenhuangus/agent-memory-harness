---
type: Fix
title: mem_8bb00bee
description: When deleting from multiple parallel lists that must stay in sync, collect indices during a read-only pass, then delete from all lists in reverse order to prevent index shift errors.
resource: 'memeval://memory/mem_8bb00bee'
timestamp: '2026-06-27T22:09:08.998564+00:00'
x_item_id: mem_8bb00bee
x_relevancy: 0.8
x_version: 1
x_session_id: 2c3f89d6-daec-4bdf-ac0a-b3e7057733b3
x_source: daydream
x_tokens: 45
---

When deleting from multiple parallel lists that must stay in sync, collect indices during a read-only pass, then delete from all lists in reverse order to prevent index shift errors.
